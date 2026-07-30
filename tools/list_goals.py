from collections.abc import Generator
from typing import Any

from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage

from utils.client import CalleApiError, CalleClient, normalize_base_url


def _bounded_limit(value: Any) -> int:
    try:
        return max(1, min(100, int(float(value))))
    except (TypeError, ValueError):
        return 50


class ListGoalsTool(Tool):
    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage, None, None]:
        try:
            credentials = self.runtime.credentials
            client = CalleClient(
                api_key=credentials.get("api_key"),
                base_url=normalize_base_url(credentials.get("base_url")),
            )
            cursor = str(tool_parameters.get("cursor") or "").strip() or None
            response = client.list_goals(cursor=cursor, limit=_bounded_limit(tool_parameters.get("limit")))
            yield self.create_json_message(response)
        except CalleApiError as error:
            yield self.create_text_message(str(error))
