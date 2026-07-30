from collections.abc import Generator
from typing import Any

from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage

from utils.client import (
    CalleApiError,
    CalleClient,
    build_goal_run_payload,
    ensure_live_phone_allowed,
    mask_phone,
    normalize_base_url,
    parse_bool,
    parse_goal_run_variables,
    redact_phone_fields,
    validate_e164,
)


def _bounded_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(float(value))
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


class CreateGoalRunAndWaitTool(Tool):
    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage, None, None]:
        try:
            goal_id = str(tool_parameters.get("goal_id") or "").strip()
            idempotency_key = str(tool_parameters.get("idempotency_key") or "").strip()
            phone_number = validate_e164(tool_parameters.get("phone_number"))
            variables = parse_goal_run_variables(
                tool_parameters.get("variables_json"), field_name="variables_json"
            )
            if not goal_id or not idempotency_key:
                yield self.create_text_message("goal_id and idempotency_key are required for Goal Run creation.")
                return

            poll_interval_seconds = _bounded_int(
                tool_parameters.get("poll_interval_seconds"), default=5, minimum=1, maximum=60
            )
            wait_timeout_minutes = _bounded_int(
                tool_parameters.get("wait_timeout_minutes"), default=15, minimum=1, maximum=60
            )
            dry_run = parse_bool(tool_parameters.get("dry_run"), default=True)
            confirm_live_call = parse_bool(tool_parameters.get("confirm_live_call"), default=False)
            payload = build_goal_run_payload(phone_number, variables)
            preview = {
                "dry_run": dry_run,
                "live_call_created": False,
                "will_create_live_call": (not dry_run) and confirm_live_call,
                "goal_id": goal_id,
                "idempotency_key": idempotency_key,
                "masked_phone": mask_phone(phone_number),
                "variables": variables,
                "poll_interval_seconds": poll_interval_seconds,
                "wait_timeout_minutes": wait_timeout_minutes,
            }
            if dry_run or not confirm_live_call:
                yield self.create_text_message(
                    "Dry run preview only. No live CALL-E Goal Run was created. "
                    "Set dry_run=false and confirm_live_call=true to create and wait for one live call."
                )
                yield self.create_json_message(preview)
                return

            ensure_live_phone_allowed(phone_number)
            credentials = self.runtime.credentials
            client = CalleClient(
                api_key=credentials.get("api_key"),
                base_url=normalize_base_url(credentials.get("base_url")),
            )
            goal_run, poll_count, timed_out = client.create_goal_run_and_wait(
                goal_id,
                payload,
                idempotency_key,
                poll_interval_seconds=poll_interval_seconds,
                wait_timeout_seconds=wait_timeout_minutes * 60,
            )
            result = {
                **preview,
                "dry_run": False,
                "live_call_created": True,
                "goal_run_id": goal_run.get("id"),
                "poll_count": poll_count,
                "timed_out": timed_out,
                "result": goal_run.get("result"),
                "error": goal_run.get("error"),
                "goal_run": redact_phone_fields(goal_run),
            }
            if timed_out:
                yield self.create_text_message(
                    "CALL-E Goal Run polling timed out. Use get_goal_run with the returned goal_run_id; do not create a duplicate Run."
                )
            yield self.create_json_message(result)
        except (ValueError, CalleApiError) as error:
            yield self.create_text_message(str(error))
