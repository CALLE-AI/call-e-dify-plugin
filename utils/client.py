from __future__ import annotations

import json
import math
import re
import time
import uuid
from typing import Any
from urllib.parse import quote

import requests


DEFAULT_BASE_URL = "https://api.heycall-e.com"
CREDENTIAL_PROBE_CALL_ID = "call_dify_credential_probe_00000000000000000000000000000000"
E164_RE = re.compile(r"^\+[1-9]\d{7,14}$")
PLACEHOLDER_PHONES = {"+15555550101", "+15555550102", "+14155550123"}
TERMINAL_STATUSES = {
    "completed",
    "failed",
    "canceled",
    "cancelled",
    "no_answer",
    "declined",
    "voicemail",
    "busy",
    "expired",
}


class CalleApiError(Exception):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        error_code: str | None = None,
        response_text: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.error_code = error_code
        self.response_text = response_text


def normalize_base_url(value: Any) -> str:
    base_url = str(value or DEFAULT_BASE_URL).strip().rstrip("/")
    if base_url.endswith("/v1"):
        base_url = base_url[:-3].rstrip("/")
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
    payload: dict[str, Any] = {"phone_number": validate_e164(phone_number)}
    if variables:
        payload["variables"] = variables
    return payload


def is_goal_run_resolved(goal_run: dict[str, Any]) -> bool:
    return goal_run.get("result") is not None or goal_run.get("error") is not None


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


def normalize_status(value: Any) -> str:
    return str(value or "").strip().lower().replace(" ", "_").replace("-", "_")


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


def _api_error(prefix: str, response: requests.Response) -> CalleApiError:
    code, message = _error_code_and_message(response)
    status = getattr(response, "status_code", None)
    text = str(getattr(response, "text", ""))[:1000]
    parts = [f"{prefix}: HTTP {status}"]
    if code:
        parts.append(code)
    if message:
        parts.append(message)
    elif text:
        parts.append(text)
    return CalleApiError(
        ": ".join(parts),
        status_code=status,
        error_code=code or None,
        response_text=text,
    )


def latest(values: list[Any]) -> Any:
    return values[-1] if values else None


def result_schema() -> dict[str, Any]:
    return {
        "type": "object",
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


def build_create_payload(
    *,
    phone_number: str,
    task: str,
    region: str,
    locale: str,
    metadata: dict[str, Any],
) -> dict[str, Any]:
    schema = result_schema()
    return {
        "task": task,
        "recipients": [
            {
                "phones": [phone_number],
                "region": region or "US",
                "locale": locale or "en-US",
            }
        ],
        "result_schema": schema,
        "recipient_result_schema": schema,
        "metadata": {**metadata, "source": "dify_call_e_plugin"},
    }


def extract_call_id(call: dict[str, Any]) -> str:
    candidates = [
        call.get("id"),
        call.get("call_id"),
        call.get("callId"),
        call.get("uuid"),
        as_object(call.get("data")).get("id"),
        as_object(call.get("data")).get("call_id"),
        as_object(call.get("data")).get("callId"),
        as_object(call.get("call")).get("id"),
        as_object(call.get("result")).get("id"),
    ]
    for candidate in candidates:
        if candidate not in (None, ""):
            return str(candidate)
    return ""


def parse_call_result(call: dict[str, Any], *, metadata_sent: dict[str, Any] | None = None) -> dict[str, Any]:
    recipient = as_object(latest(as_list(call.get("recipients"))))
    attempts = [as_object(attempt) for attempt in as_list(recipient.get("attempts"))]
    latest_attempt = as_object(latest(attempts))
    transcript_turns = []
    for attempt in attempts:
        transcript_turns.extend(as_list(attempt.get("transcript_turns")))

    structured_result = as_object(recipient.get("structured_result")) or as_object(call.get("structured_result"))
    raw_failure_code = latest_attempt.get("failure_code") or call.get("failure_code")
    failure_code = normalize_status(raw_failure_code)
    call_status = call.get("status")
    recipient_status = recipient.get("status")
    latest_attempt_status = latest_attempt.get("status")
    failed_statuses = {"failed", "canceled", "cancelled", "no_answer", "declined", "busy", "expired"}
    failed = (
        normalize_status(call_status) in failed_statuses
        or normalize_status(recipient_status) in failed_statuses
        or normalize_status(latest_attempt_status) in failed_statuses
    )
    answered = latest_attempt_status == "completed" or recipient_status == "completed"
    metadata_returned = as_object(call.get("metadata"))
    metadata_sent = metadata_sent or {}
    metadata_keys = sorted(set(metadata_sent.keys()) | {"lead_id", "notion_page_id", "company", "property", "campaign"})
    missing_or_different = [key for key in metadata_keys if metadata_sent.get(key) != metadata_returned.get(key)]

    return {
        "call_id": extract_call_id(call),
        "call_status": call_status,
        "recipient_status": recipient_status,
        "latest_attempt_status": latest_attempt_status,
        "failure_code": raw_failure_code,
        "failure_message": latest_attempt.get("failure_message") or call.get("failure_message"),
        "answered": answered,
        "no_answer": failure_code == "no_answer",
        "busy": failure_code == "busy",
        "failed": failed,
        "metadata_returned": metadata_returned,
        "metadata_round_trip": {
            "requested_keys": metadata_keys,
            "all_requested_keys_returned": len(missing_or_different) == 0,
            "missing_or_different_keys": missing_or_different,
        },
        "summary": structured_result.get("summary")
        or recipient.get("summary")
        or call.get("summary")
        or latest_attempt.get("summary"),
        "transcript": redact_phone_fields(transcript_turns),
        "structured_result": structured_result,
        "task_completed": call.get("task_completed"),
        "completion_confidence": call.get("completion_confidence"),
        "evidence": as_list(call.get("evidence")),
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
        }
        if query:
            request_kwargs["params"] = query
        try:
            response = requests.request(method, url, **request_kwargs)
        except requests.RequestException as error:
            raise CalleApiError(f"CALL-E API request failed: {error}") from error

        if response.status_code < 200 or response.status_code >= 300:
            raise _api_error("CALL-E API request failed", response)

        if not response.text:
            return {}
        try:
            payload = response.json()
        except ValueError as error:
            raise CalleApiError("CALL-E API returned non-JSON response.") from error
        return payload if isinstance(payload, dict) else {"data": payload}

    def validate_credentials(self) -> dict[str, Any]:
        url = f"{self.base_url}/v1/calls/{CREDENTIAL_PROBE_CALL_ID}"
        try:
            response = requests.request(
                "GET",
                url,
                headers=self._headers(),
                timeout=10,
            )
        except requests.RequestException as error:
            raise CalleApiError(f"Unable to reach CALL-E API while validating credentials: {error}") from error

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
                response_text=response.text[:1000],
            )

        if response.status_code == 403 or code == "forbidden":
            raise CalleApiError(
                "CALL-E API key is valid but does not have access to this project or operation.",
                status_code=response.status_code,
                error_code=code or "forbidden",
                response_text=response.text[:1000],
            )

        raise _api_error("CALL-E credential validation failed", response)

    def health(self) -> dict[str, Any]:
        url = f"{self.base_url}/health"
        try:
            response = requests.request(
                "GET",
                url,
                headers=self._headers(),
                timeout=10,
            )
        except requests.RequestException as error:
            raise CalleApiError(f"CALL-E health check failed: {error}") from error

        if response.status_code < 200 or response.status_code >= 300:
            raise CalleApiError(
                f"CALL-E health check failed: HTTP {response.status_code}: {response.text[:1000]}"
            )

        if not response.text:
            return {}

        try:
            payload = response.json()
        except (AttributeError, ValueError):
            return {"status": response.text.strip()}

        return payload if isinstance(payload, dict) else {"data": payload}

    def create_call(self, payload: dict[str, Any], *, idempotency_key: str | None = None) -> dict[str, Any]:
        url = f"{self.base_url}/v1/calls"
        try:
            response = requests.post(
                url,
                headers=self._headers(idempotency_key=idempotency_key),
                json=payload,
                timeout=60,
            )
        except requests.RequestException as error:
            raise CalleApiError(f"CALL-E create call request failed: {error}") from error

        if response.status_code < 200 or response.status_code >= 300:
            raise CalleApiError(f"CALL-E create call failed: HTTP {response.status_code}: {response.text[:1000]}")
        try:
            body = response.json()
        except ValueError as error:
            raise CalleApiError("CALL-E create call returned non-JSON response.") from error
        return body if isinstance(body, dict) else {"data": body}

    def get_call(self, call_id: str) -> dict[str, Any]:
        call_id = str(call_id or "").strip()
        if not call_id:
            raise CalleApiError("call_id is required.")
        return self._request("GET", f"/v1/calls/{call_id}", timeout=60)

    def list_goals(self, *, cursor: str | None = None, limit: int | None = None) -> dict[str, Any]:
        query: dict[str, Any] = {}
        if cursor:
            query["cursor"] = str(cursor).strip()
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
        stable_key = _required_idempotency_key(idempotency_key)
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
        poll_count = 0
        started = time.monotonic()
        latest_goal_run = goal_run

        while not is_goal_run_resolved(latest_goal_run):
            if time.monotonic() - started >= wait_timeout_seconds:
                latest_goal_run = dict(latest_goal_run)
                latest_goal_run["_dify_poll_timeout"] = True
                return latest_goal_run, poll_count, True
            time.sleep(max(1, poll_interval_seconds))
            poll_count += 1
            latest_goal_run = self.get_goal_run(goal_id, goal_run_id)

        return latest_goal_run, poll_count, False

    def create_and_wait(
        self,
        payload: dict[str, Any],
        *,
        poll_interval_seconds: int,
        wait_timeout_seconds: int,
        idempotency_key: str | None = None,
    ) -> tuple[dict[str, Any], int, bool]:
        call = self.create_call(payload, idempotency_key=idempotency_key)
        call_id = extract_call_id(call)
        if not call_id:
            raise CalleApiError("CALL-E create response did not include a recognized call ID.")

        poll_count = 0
        started = time.monotonic()
        latest_call = call

        while normalize_status(latest_call.get("status")) not in TERMINAL_STATUSES:
            if time.monotonic() - started >= wait_timeout_seconds:
                latest_call = dict(latest_call)
                latest_call["_dify_poll_timeout"] = True
                return latest_call, poll_count, True
            time.sleep(max(1, poll_interval_seconds))
            poll_count += 1
            latest_call = self.get_call(call_id)

        return latest_call, poll_count, False


def new_idempotency_key(prefix: str = "dify") -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _path_identifier(value: Any, field_name: str) -> str:
    identifier = str(value or "").strip()
    if not identifier:
        raise CalleApiError(f"{field_name} is required.")
    return quote(identifier, safe="")


def _required_idempotency_key(value: Any) -> str:
    key = str(value or "").strip()
    if not key:
        raise CalleApiError("idempotency_key is required for Goal Run creation.")
    return key


def _goal_run_id(goal_run: dict[str, Any]) -> str:
    for candidate in (goal_run.get("id"), goal_run.get("goal_run_id"), as_object(goal_run.get("data")).get("id")):
        if candidate not in (None, ""):
            return str(candidate)
    raise CalleApiError("CALL-E Goal Run create response did not include an id.")
