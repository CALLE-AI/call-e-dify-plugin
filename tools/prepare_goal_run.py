from collections.abc import Generator
from typing import Any

from dify_plugin import Tool
from dify_plugin.entities import I18nObject, ParameterOption
from dify_plugin.entities.tool import ToolInvokeMessage

from utils.client import CalleApiError, CalleClient, normalize_base_url
from utils.goal_configuration import (
    GoalConfigurationError,
    goal_interface,
    prepare_configuration,
)
from utils.messages import result_messages


class PrepareGoalRunTool(Tool):
    def _client(self) -> CalleClient:
        credentials = self.runtime.credentials
        return CalleClient(
            api_key=credentials.get("api_key"),
            base_url=normalize_base_url(credentials.get("base_url")),
        )

    def _fetch_parameter_options(self, parameter: str) -> list[ParameterOption]:
        if parameter != "goal_id":
            raise ValueError("Only goal_id supports dynamic options.")
        client = self._client()
        options = []
        after = None
        seen = set()
        while True:
            page = client.list_goals(after=after, limit=100)
            if not isinstance(page.get("data"), list):
                raise ValueError("The Goal catalog must return a data array.")
            for item in page["data"]:
                goal = goal_interface(item)
                label = f"{goal.get('title') or goal['id']} — v{goal['published_run_spec']['version']} ({goal['id']})"
                options.append(
                    ParameterOption(value=goal["id"], label=I18nObject(en_US=label))
                )
            after = page.get("next_cursor")
            if after is None:
                return options
            if not isinstance(after, str) or not after or after in seen:
                raise ValueError("The Goal catalog returned a non-progressing cursor.")
            seen.add(after)

    def _invoke(
        self, tool_parameters: dict[str, Any]
    ) -> Generator[ToolInvokeMessage, None, None]:
        try:
            goal_id = str(tool_parameters.get("goal_id") or "").strip()
            if not goal_id:
                raise GoalConfigurationError(
                    "goal_required",
                    "goal_id",
                    "Choose one published Goal from the selector.",
                )
            goal = goal_interface(self._client().get_goal(goal_id), goal_id)
            result = prepare_configuration(
                goal,
                tool_parameters.get("variables_json"),
                tool_parameters.get("reviewed_goal_json"),
            )
        except (GoalConfigurationError, CalleApiError) as error:
            finding = (
                error.finding
                if isinstance(error, GoalConfigurationError)
                else {
                    "code": error.error_code or "goal_lookup_failed",
                    "path": "goal_id",
                    "message": str(error),
                }
            )
            result = {
                "ready": False,
                "goal_id": "",
                "run_spec_id": "",
                "run_spec_version": 0,
                "goal_json": "",
                "input_fields": [],
                "result_fields": [],
                "variables_json": "",
                "errors": [finding],
                "changes": [],
                "report": finding["message"],
            }
        yield self.create_text_message(result["report"])
        yield from result_messages(self, result)
