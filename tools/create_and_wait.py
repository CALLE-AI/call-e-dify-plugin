from collections.abc import Generator
from typing import Any

from dify_plugin import Tool
from dify_plugin.entities.tool import ToolInvokeMessage

from utils.client import (
    CalleApiError,
    CalleClient,
    build_create_payload,
    creation_error_result,
    ensure_live_phone_allowed,
    extract_call_id,
    mask_phone,
    normalize_base_url,
    parse_bool,
    parse_call_result,
    parse_json_object,
    redact_phone_fields,
    required_idempotency_key,
    validate_e164,
)
from utils.messages import result_messages


def _bounded_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(float(value))
    except Exception:
        parsed = default
    return max(minimum, min(maximum, parsed))


class CreateAndWaitTool(Tool):
    def _invoke(self, tool_parameters: dict[str, Any]) -> Generator[ToolInvokeMessage, None, None]:
        idempotency_key = ""
        try:
            idempotency_key = required_idempotency_key(tool_parameters.get("idempotency_key"))
            phone_number = validate_e164(tool_parameters.get("phone_number"))
            task = str(tool_parameters.get("task") or "").strip()
            if not task:
                yield self.create_text_message("task is required.")
                return

            dry_run = parse_bool(tool_parameters.get("dry_run"), default=True)
            confirm_live_call = parse_bool(tool_parameters.get("confirm_live_call"), default=False)
            metadata = parse_json_object(tool_parameters.get("metadata_json"), field_name="metadata_json")
            region = str(tool_parameters.get("region") or "").strip()
            locale = str(tool_parameters.get("locale") or "").strip()
            poll_interval_seconds = _bounded_int(
                tool_parameters.get("poll_interval_seconds"), default=5, minimum=1, maximum=60
            )
            wait_timeout_minutes = _bounded_int(
                tool_parameters.get("wait_timeout_minutes"), default=15, minimum=1, maximum=60
            )
            payload = build_create_payload(
                phone_number=phone_number,
                task=task,
                region=region,
                locale=locale,
                metadata=metadata,
                schema=tool_parameters.get("result_schema"),
            )
            preview = {
                "dry_run": dry_run,
                "live_call_created": False,
                "will_create_live_call": (not dry_run) and confirm_live_call,
                "masked_phone": mask_phone(phone_number),
                "idempotency_key": idempotency_key,
                "region": region,
                "locale": locale,
                "task": task,
                "metadata": redact_phone_fields(payload["metadata"]),
                "result_schema": payload["result_schema"],
                "poll_interval_seconds": poll_interval_seconds,
                "wait_timeout_minutes": wait_timeout_minutes,
            }

            if dry_run or not confirm_live_call:
                yield self.create_text_message(
                    "Dry run preview only. No live CALL-E call was created. "
                    "Set dry_run=false and confirm_live_call=true to create and wait for one live call."
                )
                yield from result_messages(self, preview)
                return

            ensure_live_phone_allowed(phone_number)
            credentials = self.runtime.credentials
            client = CalleClient(
                api_key=credentials.get("api_key"),
                base_url=normalize_base_url(credentials.get("base_url")),
            )
            call, poll_count, timed_out = client.create_and_wait(
                payload,
                poll_interval_seconds=poll_interval_seconds,
                wait_timeout_seconds=wait_timeout_minutes * 60,
                idempotency_key=idempotency_key,
            )
            parsed = parse_call_result(call, metadata_sent=payload["metadata"])
            call_id = extract_call_id(call)
            result = {
                **preview,
                "dry_run": False,
                "live_call_created": True,
                "idempotency_key": idempotency_key,
                "call_id": call_id,
                "poll_count": poll_count,
                "timed_out": timed_out,
                "result": parsed,
            }

            status = parsed.get("call_status") or "unknown"
            if timed_out:
                yield self.create_text_message(
                    f"CALL-E polling timed out. Call ID: {call_id}. Use get_call to resume; do not create a replacement call."
                )
            else:
                yield self.create_text_message(
                    f"CALL-E call result ready. Call ID: {call_id}. Status: {status}. Result status: {parsed.get('result_status')}."
                )
            yield from result_messages(self, result)
        except CalleApiError as error:
            yield self.create_text_message(str(error))
            yield from result_messages(self, creation_error_result(error, idempotency_key))
        except ValueError as error:
            yield self.create_text_message(str(error))
        except Exception as error:
            yield self.create_text_message(f"Unexpected CALL-E create_and_wait error: {error}")
