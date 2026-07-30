from collections.abc import Generator
from typing import Any

from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage

from utils.client import CalleApiError, CalleClient, normalize_base_url


class GetGoalTool(Tool):
    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage, None, None]:
        try:
            credentials = self.runtime.credentials
            client = CalleClient(
                api_key=credentials.get("api_key"),
                base_url=normalize_base_url(credentials.get("base_url")),
            )
            response = client.get_goal(str(tool_parameters.get("goal_id") or ""))
            yield self.create_json_message(response)
        except CalleApiError as error:
            yield self.create_text_message(str(error))
