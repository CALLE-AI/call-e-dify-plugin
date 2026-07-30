import pytest

from utils import client
from utils.client import (
    CalleClient,
    CalleApiError,
    build_goal_run_payload,
    build_create_payload,
    is_goal_run_resolved,
    mask_phone,
    normalize_base_url,
    parse_goal_run_variables,
    parse_json_object,
    validate_e164,
)


EXPECTED_CREDENTIAL_PROBE_CALL_ID = "call_dify_credential_probe_00000000000000000000000000000000"


def test_normalize_base_url_strips_v1_suffix():
    assert normalize_base_url("https://api.example.com/v1") == "https://api.example.com"
    assert normalize_base_url("https://api.example.com/") == "https://api.example.com"


def test_validate_e164_accepts_valid_number():
    assert validate_e164("+15555550123") == "+15555550123"


def test_validate_e164_rejects_local_number():
    with pytest.raises(ValueError):
        validate_e164("555-0101")


def test_mask_phone_hides_middle_digits():
    assert mask_phone("+15555550123") == "+15****23"


def test_parse_json_object_requires_object():
    assert parse_json_object('{"lead_id":"lead_1"}', field_name="metadata_json") == {
        "lead_id": "lead_1"
    }
    with pytest.raises(ValueError):
        parse_json_object("[1, 2]", field_name="metadata_json")


def test_build_create_payload_adds_source_metadata():
    payload = build_create_payload(
        phone_number="+15555550123",
        task="Call an authorized test number.",
        region="US",
        locale="en-US",
        metadata={"lead_id": "lead_1"},
    )
    assert payload["recipients"][0]["phones"] == ["+15555550123"]
    assert payload["metadata"]["lead_id"] == "lead_1"
    assert payload["metadata"]["source"] == "dify_call_e_plugin"


def test_build_goal_run_payload_keeps_only_documented_fields():
    assert build_goal_run_payload(
        "+14155550100",
        {"order_id": "ORD-8472", "priority": True},
    ) == {
        "phone_number": "+14155550100",
        "variables": {"order_id": "ORD-8472", "priority": True},
    }


def test_goal_run_variables_reject_nested_and_non_finite_values():
    with pytest.raises(ValueError, match="variables_json.order"):
        parse_goal_run_variables('{"order":{"id":"ORD-8472"}}', field_name="variables_json")
    with pytest.raises(ValueError, match="variables_json.score"):
        parse_goal_run_variables('{"score": NaN}', field_name="variables_json")


def test_goal_run_resolution_requires_result_or_error():
    assert is_goal_run_resolved({"status": "completed", "result": None, "error": None}) is False
    assert is_goal_run_resolved(
        {"status": "completed", "result": {"confirmed": True}, "error": None}
    ) is True
    assert is_goal_run_resolved(
        {"status": "failed", "result": None, "error": {"code": "no_answer"}}
    ) is True


def test_create_goal_run_posts_closed_payload_and_required_idempotency_header(monkeypatch):
    class Response:
        status_code = 201
        text = '{"id":"goal_run_1","result":null,"error":null}'

        def json(self):
            return {"id": "goal_run_1", "result": None, "error": None}

    captured = {}

    def fake_request(method, url, headers, json, timeout, **kwargs):
        captured.update(
            method=method,
            url=url,
            headers=headers,
            json=json,
            timeout=timeout,
            kwargs=kwargs,
        )
        return Response()

    monkeypatch.setattr(client.requests, "request", fake_request)

    result = CalleClient(api_key="test_key", base_url="https://api.example.com").create_goal_run(
        "goal_1",
        {"phone_number": "+14155550100"},
        idempotency_key="delivery:ORD-8472:v1",
    )

    assert result == {"id": "goal_run_1", "result": None, "error": None}
    assert captured == {
        "method": "POST",
        "url": "https://api.example.com/v1/goals/goal_1/runs",
        "headers": {
            "Accept": "application/json",
            "Authorization": "Bearer test_key",
            "Content-Type": "application/json",
            "Idempotency-Key": "delivery:ORD-8472:v1",
        },
        "json": {"phone_number": "+14155550100"},
        "timeout": 60,
        "kwargs": {},
    }


def test_create_goal_run_requires_a_non_empty_idempotency_key():
    with pytest.raises(CalleApiError, match="idempotency_key is required"):
        CalleClient(api_key="test_key").create_goal_run(
            "goal_1",
            {"phone_number": "+14155550100"},
            idempotency_key=" ",
        )


def test_goal_run_wait_keeps_polling_after_completed_until_result_is_persisted(monkeypatch):
    api = CalleClient(api_key="test_key")
    monkeypatch.setattr(
        api,
        "create_goal_run",
        lambda *_args, **_kwargs: {"id": "goal_run_1", "result": None, "error": None},
    )
    responses = iter(
        [
            {"id": "goal_run_1", "status": "completed", "result": None, "error": None},
            {
                "id": "goal_run_1",
                "status": "completed",
                "result": {"confirmed": True},
                "error": None,
            },
        ]
    )
    monkeypatch.setattr(api, "get_goal_run", lambda *_args: next(responses))
    monkeypatch.setattr(client.time, "sleep", lambda _seconds: None)

    result, poll_count, timed_out = api.create_goal_run_and_wait(
        "goal_1",
        {"phone_number": "+14155550100"},
        "delivery:ORD-8472:v1",
        poll_interval_seconds=1,
        wait_timeout_seconds=60,
    )

    assert result["result"] == {"confirmed": True}
    assert poll_count == 2
    assert timed_out is False


def test_health_accepts_text_ok_response(monkeypatch):
    class Response:
        status_code = 200
        text = "OK"

    def fake_request(method, url, headers, timeout, **kwargs):
        assert method == "GET"
        assert url == "https://api.example.com/health"
        assert headers["Authorization"] == "Bearer test_key"
        assert timeout == 10
        assert kwargs.get("json") is None
        return Response()

    monkeypatch.setattr(client.requests, "request", fake_request)

    assert CalleClient(api_key="test_key", base_url="https://api.example.com").health() == {
        "status": "OK"
    }


def test_validate_credentials_accepts_documented_not_found_probe(monkeypatch):
    class Response:
        status_code = 404
        text = '{"error":{"code":"not_found","message":"Call task not found.","details":{}}}'

        def json(self):
            return {"error": {"code": "not_found", "message": "Call task not found.", "details": {}}}

    calls = []

    def fake_request(method, url, headers, timeout, **kwargs):
        calls.append((method, url, headers, timeout, kwargs))
        return Response()

    monkeypatch.setattr(client.requests, "request", fake_request)

    result = CalleClient(api_key="test_key", base_url="https://api.example.com").validate_credentials()

    assert result == {
        "authenticated": True,
        "probe_call_id": EXPECTED_CREDENTIAL_PROBE_CALL_ID,
        "probe_status_code": 404,
    }
    assert calls == [
        (
            "GET",
            f"https://api.example.com/v1/calls/{EXPECTED_CREDENTIAL_PROBE_CALL_ID}",
            {
                "Accept": "application/json",
                "Authorization": "Bearer test_key",
                "Content-Type": "application/json",
            },
            10,
            {},
        )
    ]


def test_validate_credentials_rejects_unauthorized_probe(monkeypatch):
    class Response:
        status_code = 401
        text = '{"error":{"code":"unauthorized","message":"Invalid or missing API key.","details":{}}}'

        def json(self):
            return {
                "error": {
                    "code": "unauthorized",
                    "message": "Invalid or missing API key.",
                    "details": {},
                }
            }

    def fake_request(method, url, headers, timeout, **kwargs):
        return Response()

    monkeypatch.setattr(client.requests, "request", fake_request)

    with pytest.raises(CalleApiError, match="CALL-E API key is invalid or missing"):
        CalleClient(api_key="bad_key", base_url="https://api.example.com").validate_credentials()
