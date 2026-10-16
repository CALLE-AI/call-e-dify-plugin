from collections.abc import Generator
from typing import Any

from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage

from utils.client import CalleApiError, CalleClient, normalize_base_url, parse_call_result
from utils.messages import result_messages


class GetCallTool(Tool):
    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage, None, None]:
        call_id = str(tool_parameters.get("call_id") or "").strip()
        if not call_id:
            yield self.create_text_message("call_id is required.")
            return

        try:
            credentials = self.runtime.credentials
            client = CalleClient(
                api_key=credentials.get("api_key"),
                base_url=normalize_base_url(credentials.get("base_url")),
            )
            call = client.get_call(call_id)
            parsed = parse_call_result(call)
            yield self.create_text_message(
                f"CALL-E call fetched. Status: {parsed.get('call_status') or 'unknown'}."
            )
            yield from result_messages(self, parsed)
        except CalleApiError as error:
            yield self.create_text_message(str(error))
        except Exception as error:
            yield self.create_text_message(f"Unexpected CALL-E get_call error: {error}")
