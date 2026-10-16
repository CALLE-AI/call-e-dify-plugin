from collections.abc import Generator
from typing import Any

from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage


def result_messages(tool: Tool, result: dict[str, Any]) -> Generator[ToolInvokeMessage, None, None]:
    yield tool.create_json_message(result)
    # Dify populates named workflow outputs from variable messages, not JSON messages.
    for name, value in result.items():
        yield tool.create_variable_message(name, value)
