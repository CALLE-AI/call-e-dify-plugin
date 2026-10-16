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


class CreateCallTool(Tool):
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
            payload = build_create_payload(
                phone_number=phone_number,
                task=task,
                region=region,
                locale=locale,
                metadata=metadata,
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
            }

            if dry_run or not confirm_live_call:
                yield self.create_text_message(
                    "Dry run preview only. No live CALL-E call was created. "
                    "Set dry_run=false and confirm_live_call=true to create one live call."
                )
                yield from result_messages(self, preview)
                return

            ensure_live_phone_allowed(phone_number)
            credentials = self.runtime.credentials
            client = CalleClient(
                api_key=credentials.get("api_key"),
                base_url=normalize_base_url(credentials.get("base_url")),
            )
            call = client.create_call(payload, idempotency_key=idempotency_key)
            parsed = parse_call_result(call, metadata_sent=payload["metadata"])
            call_id = extract_call_id(call)
            result = {
                **preview,
                "dry_run": False,
                "live_call_created": True,
                "idempotency_key": idempotency_key,
                "call_id": call_id,
                "result": parsed,
            }

            yield self.create_text_message(f"CALL-E call created. Call ID: {call_id or 'unknown'}.")
            yield from result_messages(self, result)
        except CalleApiError as error:
            yield self.create_text_message(str(error))
            yield from result_messages(self, creation_error_result(error, idempotency_key))
        except ValueError as error:
            yield self.create_text_message(str(error))
        except Exception as error:
            yield self.create_text_message(f"Unexpected CALL-E create_call error: {error}")
