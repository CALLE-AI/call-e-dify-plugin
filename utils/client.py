from __future__ import annotations

import json
import math
import re
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import quote, urlsplit

import requests


DEFAULT_BASE_URL = "https://api.heycall-e.com"
CREDENTIAL_PROBE_CALL_ID = "call_dify_credential_probe_00000000000000000000000000000000"
E164_RE = re.compile(r"^\+[1-9]\d{7,14}$")
PLACEHOLDER_PHONES = {"+15555550101", "+15555550102", "+14155550123"}
FINAL_RESULT_STATUSES = {"available", "unavailable", "not_applicable"}


class CalleApiError(Exception):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        error_code: str | None = None,
        response_text: str | None = None,
        details: dict[str, Any] | None = None,
        accepted_resource: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.error_code = error_code
        self.response_text = response_text
        self.details = details or {}
        self.accepted_resource = accepted_resource


def normalize_base_url(value: Any) -> str:
    base_url = str(value or DEFAULT_BASE_URL).strip().rstrip("/")
    if base_url.endswith(("/v1", "/v2")):
        base_url = base_url[:-3].rstrip("/")
    parsed = urlsplit(base_url)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise CalleApiError("CALL-E API base URL must be an HTTP(S) URL without credentials, query, or fragment.")
    return base_url or DEFAULT_BASE_URL


def parse_bool(value: Any, default: bool = False) -> bool:
    if value is None or value == "":
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def parse_json_object(value: Any, *, field_name: str) -> dict[str, Any]:
    if value in (None, ""):
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as error:
            raise ValueError(f"{field_name} must be a valid JSON object string.") from error
        if isinstance(parsed, dict):
            return parsed
    raise ValueError(f"{field_name} must be a JSON object.")


def parse_goal_run_variables(value: Any, *, field_name: str) -> dict[str, str | bool | int | float]:
    variables = parse_json_object(value, field_name=field_name)
    for key, item in variables.items():
        valid_scalar = isinstance(item, (str, bool, int, float))
        finite_number = not isinstance(item, float) or math.isfinite(item)
        if not valid_scalar or not finite_number:
            raise ValueError(
                f"{field_name}.{key} must be a finite string, boolean, or number."
            )
    return variables


def mask_phone(phone: Any) -> str:
    value = str(phone or "").strip()
    if not value:
        return ""
    if len(value) <= 5:
        return "****"
    return value[:3] + "****" + value[-2:]


def validate_e164(phone: Any) -> str:
    value = str(phone or "").strip()
    if not E164_RE.match(value):
        raise ValueError("phone_number must be in E.164 format, for example +15555550123.")
    return value


def build_goal_run_payload(
    phone_number: str,
    variables: dict[str, str | bool | int | float],
) -> dict[str, Any]:
    payload: dict[str, Any] = {"phone": validate_e164(phone_number)}
    if variables:
        payload["variables"] = variables
    return payload


def is_result_resolved(resource: dict[str, Any]) -> bool:
    status = resource.get("result_status")
    if status not in FINAL_RESULT_STATUSES | {"pending"}:
        raise CalleApiError("CALL-E response has a missing or unknown result_status; retain the original ID and request.")
    return status != "pending"


def ensure_live_phone_allowed(phone: str) -> None:
    if phone in PLACEHOLDER_PHONES:
        raise ValueError(
            "Replace the placeholder phone number with a number you own or are explicitly authorized to call."
        )


def redact_phone_fields(value: Any) -> Any:
    if isinstance(value, list):
        return [redact_phone_fields(item) for item in value]
    if isinstance(value, dict):
        output = {}
        for key, item in value.items():
            lowered = str(key).lower()
            if lowered in {"phone", "phones"} or "phone_number" in lowered:
                output[key] = [mask_phone(v) for v in item] if isinstance(item, list) else mask_phone(item)
            else:
                output[key] = redact_phone_fields(item)
        return output
    return value


def as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def as_object(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _response_json(response: requests.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _error_code_and_message(response: requests.Response) -> tuple[str, str]:
    payload = _response_json(response)
    error = as_object(payload.get("error"))
    code = str(error.get("code") or "").strip()
    message = str(error.get("message") or "").strip()
    return code, message


def _api_error(prefix: str, response: requests.Response, api_key: str) -> CalleApiError:
    code, message = _error_code_and_message(response)
    status = getattr(response, "status_code", None)
    text = str(getattr(response, "text", "")).replace(api_key, "[redacted]")[:1000]
    parts = [f"{prefix}: HTTP {status}"]
    if code:
        parts.append(code)
    if message:
        parts.append(message.replace(api_key, "[redacted]"))
    return CalleApiError(
        ": ".join(parts),
        status_code=status,
        error_code=code or None,
        response_text=text,
        details=redact_phone_fields(as_object(as_object(_response_json(response).get("error")).get("details"))),
    )


def result_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["call_completed", "outcome", "summary", "next_action"],
        "properties": {
            "call_completed": {"type": "string", "enum": ["yes", "no", "partial", "unknown"]},
            "outcome": {
                "type": "string",
                "enum": ["completed", "partial", "no_answer", "voicemail", "busy", "failed", "unknown"],
            },
            "summary": {"type": "string"},
            "next_action": {"type": "string", "enum": ["none", "follow_up", "retry_later", "manual_review"]},
            "language_detected": {"type": "string"},
            "participant_response": {"type": "string"},
            "key_points": {"type": "string"},
            "issues": {"type": "string"},
            "audio_or_connection_notes": {"type": "string"},
        },
    }


def parse_result_schema(value: Any) -> dict[str, Any]:
    if value is None or (isinstance(value, str) and not value.strip()):
        return result_schema()

    def unique_object(pairs):
        parsed = dict(pairs)
        if len(parsed) != len(pairs):
            raise ValueError("result_schema must not repeat JSON object keys.")
        return parsed

    if isinstance(value, str):
        try:
            value = json.loads(value, object_pairs_hook=unique_object)
        except json.JSONDecodeError as error:
            raise ValueError("result_schema must be a valid JSON object string.") from error
    if not isinstance(value, dict):
        raise ValueError("result_schema must be a JSON object.")
    root_keywords = {"$schema", "type", "description", "additionalProperties", "properties", "required"}
    if set(value) - root_keywords:
        raise ValueError("result_schema contains unsupported root keywords.")
    if value.get("$schema") not in (None, "https://json-schema.org/draft/2020-12/schema"):
        raise ValueError("result_schema.$schema must use JSON Schema draft 2020-12.")
    if value.get("type") != "object" or value.get("additionalProperties") is not False:
        raise ValueError("result_schema requires type=object and additionalProperties=false.")
    properties = value.get("properties")
    if not isinstance(properties, dict) or len(properties) > 32:
        raise ValueError("result_schema.properties must be an object with at most 32 fields.")
    required = value.get("required", [])
    if (
        not isinstance(required, list)
        or any(not isinstance(name, str) or name not in properties for name in required)
        or len(set(required)) != len(required)
    ):
        raise ValueError("result_schema.required must list declared field names without duplicates.")
    for node in [value, *properties.values()]:
        if not isinstance(node, dict):
            raise ValueError("Each result_schema property must be a scalar schema object.")
        description = node.get("description")
        if description is not None and (not isinstance(description, str) or len(description) > 512):
            raise ValueError("result_schema descriptions must contain at most 512 characters.")
    scalar_types = {"string": (str,), "boolean": (bool,), "integer": (int,), "number": (int, float)}
    for name, schema in properties.items():
        if not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", name):
            raise ValueError("result_schema field names must match [a-z][a-z0-9_]{0,63}.")
        if set(schema) - {"type", "description", "enum"}:
            raise ValueError(f"result_schema.properties.{name} only supports type, description, and enum.")
        kind = schema.get("type")
        if not isinstance(kind, str) or kind not in scalar_types:
            raise ValueError(f"result_schema.properties.{name} must be string, boolean, integer, or number.")
        if "enum" in schema:
            values = schema["enum"]
            if not isinstance(values, list) or not 1 <= len(values) <= 32:
                raise ValueError(f"result_schema.properties.{name}.enum must contain 1-32 values.")
            if any(
                type(item) not in scalar_types[kind]
                or (isinstance(item, float) and not math.isfinite(item))
                or (type(item) is int and abs(item) > 9007199254740991)
                for item in values
            ):
                raise ValueError(f"result_schema.properties.{name}.enum values must match its finite scalar type.")
            if len(set(values)) != len(values):
                raise ValueError(f"result_schema.properties.{name}.enum must not contain duplicates.")
    encoded = json.dumps({key: item for key, item in value.items() if key != "$schema"},
                         ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
    if len(encoded) > 32768:
        raise ValueError("result_schema must not exceed 32 KiB of JSON.")
    return value


def build_create_payload(
    *,
    phone_number: str,
    task: str,
    region: str,
    locale: str,
    metadata: dict[str, Any],
    schema: Any = None,
) -> dict[str, Any]:
    payload = {
        "task": task,
        "phone": validate_e164(phone_number),
        "result_schema": parse_result_schema(schema),
        "metadata": {**metadata, "source": "dify_call_e_plugin"},
    }
    if region:
        payload["region"] = region
    if locale:
        payload["locale"] = locale
    return payload


def extract_call_id(call: dict[str, Any]) -> str:
    # The API resource id is not the telephone/Billing call_id.
    return str(call.get("id") or "")


def parse_call_result(call: dict[str, Any], *, metadata_sent: dict[str, Any] | None = None) -> dict[str, Any]:
    structured_result = call.get("result")
    error = call.get("error")
    call_outcome = call.get("call_outcome")
    metadata_returned = as_object(call.get("metadata"))
    metadata_sent = metadata_sent or {}
    metadata_keys = sorted(metadata_sent)
    missing_or_different = [key for key in metadata_keys if metadata_sent.get(key) != metadata_returned.get(key)]

    return {
        "id": extract_call_id(call),
        "call_id": extract_call_id(call),
        "billing_call_id": call.get("call_id"),
        "call_status": call.get("status"),
        "call_outcome": call_outcome,
        "result_status": call.get("result_status"),
        "error": redact_phone_fields(error),
        "failure_code": as_object(error).get("code"),
        "failure_message": as_object(error).get("message"),
        "answered": call_outcome == "completed" if call_outcome is not None else None,
        "no_answer": call_outcome == "no_answer",
        "busy": call_outcome == "busy",
        "failed": call.get("status") == "failed" or error is not None,
        "metadata_returned": redact_phone_fields(metadata_returned),
        "metadata_round_trip": {
            "requested_keys": metadata_keys,
            "all_requested_keys_returned": len(missing_or_different) == 0,
            "missing_or_different_keys": missing_or_different,
        },
        "summary": as_object(structured_result).get("summary"),
        "transcript": redact_phone_fields(as_list(call.get("transcript"))),
        "result": redact_phone_fields(structured_result),
        "structured_result": redact_phone_fields(structured_result),
        "raw_call_result": redact_phone_fields(call),
    }


class CalleClient:
    def __init__(self, *, api_key: str, base_url: str | None = None) -> None:
        self.api_key = str(api_key or "").strip()
        self.base_url = normalize_base_url(base_url)
        if not self.api_key:
            raise CalleApiError("CALL-E API key is required.")

    def _headers(self, *, idempotency_key: str | None = None) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        return headers

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        query: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
        timeout: int = 30,
    ) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        request_kwargs: dict[str, Any] = {
            "headers": self._headers(idempotency_key=idempotency_key),
            "json": json_body,
            "timeout": timeout,
            "allow_redirects": False,
        }
        if query:
            request_kwargs["params"] = query
        try:
            response = requests.request(method, url, **request_kwargs)
        except requests.RequestException as error:
            raise CalleApiError(
                "CALL-E API network request failed. For creation, retry only with the original idempotency_key and unchanged inputs."
            ) from error

        if response.status_code < 200 or response.status_code >= 300:
            raise _api_error("CALL-E API request failed", response, self.api_key)

        if not response.text:
            return {}
        try:
            payload = response.json()
        except ValueError as error:
            raise CalleApiError("CALL-E API returned non-JSON response.") from error
        if not isinstance(payload, dict):
            raise CalleApiError("CALL-E API returned a non-object response; retain the original key and request.")
        return payload

    def validate_credentials(self) -> dict[str, Any]:
        url = f"{self.base_url}/v2/calls/{CREDENTIAL_PROBE_CALL_ID}"
        try:
            response = requests.request(
                "GET",
                url,
                headers=self._headers(),
                timeout=10,
                allow_redirects=False,
            )
        except requests.RequestException as error:
            raise CalleApiError("Unable to reach CALL-E API while validating credentials.") from error

        if response.status_code == 200:
            return {
                "authenticated": True,
                "probe_call_id": CREDENTIAL_PROBE_CALL_ID,
                "probe_status_code": response.status_code,
            }

        code, _message = _error_code_and_message(response)
        if response.status_code == 404 and code == "not_found":
            return {
                "authenticated": True,
                "probe_call_id": CREDENTIAL_PROBE_CALL_ID,
                "probe_status_code": response.status_code,
            }

        if response.status_code == 401 or code == "unauthorized":
            raise CalleApiError(
                "CALL-E API key is invalid or missing.",
                status_code=response.status_code,
                error_code=code or "unauthorized",
            )

        if response.status_code == 403 or code == "forbidden":
            raise CalleApiError(
                "CALL-E API key is valid but does not have access to this project or operation.",
                status_code=response.status_code,
                error_code=code or "forbidden",
            )

        raise _api_error("CALL-E credential validation failed", response, self.api_key)

    def health(self) -> dict[str, Any]:
        url = f"{self.base_url}/health"
        try:
            response = requests.request(
                "GET",
                url,
                headers=self._headers(),
                timeout=10,
                allow_redirects=False,
            )
        except requests.RequestException as error:
            raise CalleApiError("Unable to reach CALL-E health check.") from error

        if response.status_code < 200 or response.status_code >= 300:
            raise _api_error("CALL-E health check failed", response, self.api_key)

        if not response.text:
            return {}

        try:
            payload = response.json()
        except (AttributeError, ValueError):
            return {"status": response.text.strip()}

        return payload if isinstance(payload, dict) else {"data": payload}

    def create_call(self, payload: dict[str, Any], *, idempotency_key: str) -> dict[str, Any]:
        call = self._request(
            "POST", "/v2/calls", json_body=payload,
            idempotency_key=required_idempotency_key(idempotency_key), timeout=60,
        )
        if not extract_call_id(call):
            raise CalleApiError("CALL-E create response did not include an API resource id.", accepted_resource=call)
        return call

    def get_call(self, call_id: str) -> dict[str, Any]:
        return self._request("GET", f"/v2/calls/{_path_identifier(call_id, 'call_id')}", timeout=60)

    def list_goals(self, *, after: str | None = None, limit: int | None = None) -> dict[str, Any]:
        query: dict[str, Any] = {}
        if after:
            query["after"] = after
        if limit is not None:
            query["limit"] = int(limit)
        return self._request("GET", "/v1/goals", query=query, timeout=60)

    def get_goal(self, goal_id: str) -> dict[str, Any]:
        return self._request("GET", f"/v1/goals/{_path_identifier(goal_id, 'goal_id')}", timeout=60)

    def create_goal_run(
        self,
        goal_id: str,
        payload: dict[str, Any],
        *,
        idempotency_key: str,
    ) -> dict[str, Any]:
        stable_key = required_idempotency_key(idempotency_key)
        return self._request(
            "POST",
            f"/v1/goals/{_path_identifier(goal_id, 'goal_id')}/runs",
            json_body=payload,
            idempotency_key=stable_key,
            timeout=60,
        )

    def get_goal_run(self, goal_id: str, goal_run_id: str) -> dict[str, Any]:
        return self._request(
            "GET",
            f"/v1/goals/{_path_identifier(goal_id, 'goal_id')}/runs/"
            f"{_path_identifier(goal_run_id, 'goal_run_id')}",
            timeout=60,
        )

    def create_goal_run_and_wait(
        self,
        goal_id: str,
        payload: dict[str, Any],
        idempotency_key: str,
        *,
        poll_interval_seconds: int,
        wait_timeout_seconds: int,
    ) -> tuple[dict[str, Any], int, bool]:
        goal_run = self.create_goal_run(goal_id, payload, idempotency_key=idempotency_key)
        goal_run_id = _goal_run_id(goal_run)
        return self._wait_for_result(
            goal_run, lambda: self.get_goal_run(goal_id, goal_run_id),
            poll_interval_seconds=poll_interval_seconds, wait_timeout_seconds=wait_timeout_seconds,
        )

    def create_and_wait(
        self,
        payload: dict[str, Any],
        *,
        poll_interval_seconds: int,
        wait_timeout_seconds: int,
        idempotency_key: str,
    ) -> tuple[dict[str, Any], int, bool]:
        call = self.create_call(payload, idempotency_key=idempotency_key)
        call_id = extract_call_id(call)
        return self._wait_for_result(
            call, lambda: self.get_call(call_id),
            poll_interval_seconds=poll_interval_seconds, wait_timeout_seconds=wait_timeout_seconds,
        )

    def _wait_for_result(
        self,
        resource: dict[str, Any],
        read: Callable[[], dict[str, Any]],
        *,
        poll_interval_seconds: int,
        wait_timeout_seconds: int,
    ) -> tuple[dict[str, Any], int, bool]:
        poll_count = 0
        deadline = time.monotonic() + wait_timeout_seconds
        try:
            while not is_result_resolved(resource):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return {**resource, "_dify_poll_timeout": True}, poll_count, True
                time.sleep(min(max(1, poll_interval_seconds), remaining))
                if time.monotonic() >= deadline:
                    return {**resource, "_dify_poll_timeout": True}, poll_count, True
                resource = read()
                poll_count += 1
        except CalleApiError as error:
            error.accepted_resource = resource
            raise
        return resource, poll_count, False


def _path_identifier(value: Any, field_name: str) -> str:
    identifier = str(value or "").strip()
    if not identifier or identifier in {".", ".."}:
        raise CalleApiError(f"{field_name} is required.")
    return quote(identifier, safe="")


def required_idempotency_key(value: Any) -> str:
    key = str(value or "").strip()
    if not key or len(key) > 255 or any(ord(char) < 32 or ord(char) > 126 for char in key):
        raise CalleApiError(
            "idempotency_key is required: use 1-255 printable ASCII characters for one business event. "
            "No CALL-E request was sent."
        )
    return key


def creation_error_result(error: CalleApiError, idempotency_key: str) -> dict[str, Any]:
    return {
        "idempotency_key": idempotency_key,
        "accepted_resource": redact_phone_fields(error.accepted_resource),
        "error": {
            "code": error.error_code, "message": str(error),
            "http_status": error.status_code, "details": error.details,
        },
        "recovery": (
            "Set the tool's Idempotency Key to a saved business event ID, "
            "for example order-8472-confirmation-attempt-1, then submit. "
            "Keep this key and the original inputs for retries."
            if not idempotency_key
            else "If accepted_resource includes an id, read it with get_call or get_goal_run. "
            "Otherwise acceptance may be uncertain. Retry creation only with the original key "
            "and unchanged inputs; do not generate a new key."
        ),
    }


def _goal_run_id(goal_run: dict[str, Any]) -> str:
    for candidate in (goal_run.get("id"), goal_run.get("goal_run_id"), as_object(goal_run.get("data")).get("id")):
        if candidate not in (None, ""):
            return str(candidate)
    raise CalleApiError("CALL-E Goal Run create response did not include an id.")
