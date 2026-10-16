import pytest

from utils import client
from utils.client import (
    CalleClient,
    CalleApiError,
    build_goal_run_payload,
    build_create_payload,
    is_result_resolved,
    mask_phone,
    normalize_base_url,
    parse_goal_run_variables,
    parse_call_result,
    parse_json_object,
    validate_e164,
)


EXPECTED_CREDENTIAL_PROBE_CALL_ID = "call_dify_credential_probe_00000000000000000000000000000000"


def test_normalize_base_url_strips_v1_suffix():
    assert normalize_base_url("https://api.example.com/v1") == "https://api.example.com"
    assert normalize_base_url("https://api.example.com/v2/") == "https://api.example.com"
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
    assert payload["phone"] == "+15555550123"
    assert "recipients" not in payload
    assert "recipient_result_schema" not in payload
    assert payload["result_schema"]["additionalProperties"] is False
    assert all(field["type"] in {"string", "boolean", "number", "integer"}
               for field in payload["result_schema"]["properties"].values())
    assert payload["metadata"]["lead_id"] == "lead_1"
    assert payload["metadata"]["source"] == "dify_call_e_plugin"


def test_build_goal_run_payload_keeps_only_documented_fields():
    assert build_goal_run_payload(
        "+14155550100",
        {"order_id": "ORD-8472", "priority": True},
    ) == {
        "phone": "+14155550100",
        "variables": {"order_id": "ORD-8472", "priority": True},
    }


def test_goal_run_variables_reject_nested_and_non_finite_values():
    with pytest.raises(ValueError, match="variables_json.order"):
        parse_goal_run_variables('{"order":{"id":"ORD-8472"}}', field_name="variables_json")
    with pytest.raises(ValueError, match="variables_json.score"):
        parse_goal_run_variables('{"score": NaN}', field_name="variables_json")


@pytest.mark.parametrize("status", ["available", "unavailable", "not_applicable"])
def test_resolution_uses_result_status_even_with_null_result_and_error(status):
    assert is_result_resolved({"status": "completed", "result_status": status, "result": None, "error": None})
    assert not is_result_resolved({"status": "completed", "result_status": "pending", "result": None, "error": None})


def test_resolution_rejects_legacy_or_unrecognized_response():
    with pytest.raises(CalleApiError, match="result_status"):
        is_result_resolved({"status": "completed", "result": {}})


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
        {"phone": "+14155550100"},
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
        "json": {"phone": "+14155550100"},
        "timeout": 60,
        "kwargs": {"allow_redirects": False},
    }


def test_create_goal_run_requires_a_non_empty_idempotency_key():
    with pytest.raises(CalleApiError, match="idempotency_key is required"):
        CalleClient(api_key="test_key").create_goal_run(
            "goal_1",
            {"phone": "+14155550100"},
            idempotency_key=" ",
        )


def test_goal_run_wait_keeps_polling_after_completed_until_result_is_persisted(monkeypatch):
    api = CalleClient(api_key="test_key")
    monkeypatch.setattr(
        api,
        "create_goal_run",
        lambda *_args, **_kwargs: {"id": "goal_run_1", "result_status": "pending", "result": None, "error": None},
    )
    responses = iter(
        [
            {"id": "goal_run_1", "status": "completed", "result_status": "pending", "result": None, "error": None},
            {
                "id": "goal_run_1",
                "status": "completed",
                "result_status": "available",
                "result": {"confirmed": True},
                "error": None,
            },
        ]
    )
    monkeypatch.setattr(api, "get_goal_run", lambda *_args: next(responses))
    monkeypatch.setattr(client.time, "sleep", lambda _seconds: None)

    result, poll_count, timed_out = api.create_goal_run_and_wait(
        "goal_1",
        {"phone": "+14155550100"},
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
            f"https://api.example.com/v2/calls/{EXPECTED_CREDENTIAL_PROBE_CALL_ID}",
            {
                "Accept": "application/json",
                "Authorization": "Bearer test_key",
                "Content-Type": "application/json",
            },
            10,
            {"allow_redirects": False},
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


def test_omitted_hints_stay_omitted_on_replay():
    payload = build_create_payload(phone_number="+15555550123", task="Speak English.", region="", locale="", metadata={})
    assert "region" not in payload
    assert "locale" not in payload


@pytest.mark.parametrize("result", [None, {}, {"summary": "Test received."}])
def test_parse_v2_result_preserves_null_empty_and_transcript(result):
    call = {
        "id": "call_api_1", "call_id": "billing_1", "status": "completed",
        "call_outcome": "completed", "result_status": "available" if result is not None else "unavailable",
        "result": result, "error": None,
        "transcript": [{"speaker": "user", "offset_seconds": 1, "text": "Yes."}],
        "metadata": {"lead_id": "lead_1"},
    }
    parsed = parse_call_result(call, metadata_sent={"lead_id": "lead_1"})
    assert parsed["call_id"] == call["id"]
    assert parsed["billing_call_id"] == call["call_id"]
    assert parsed["result"] == parsed["structured_result"] == result
    assert parsed["transcript"] == call["transcript"]
    assert parsed["summary"] == (result or {}).get("summary")
    assert parsed["answered"] is True
    assert parsed["metadata_round_trip"]["all_requested_keys_returned"]


def test_no_answer_is_not_a_technical_failure_and_error_details_survive():
    parsed = parse_call_result({"id": "call_1", "status": "completed", "call_outcome": "no_answer", "result": None, "error": None})
    assert parsed["no_answer"] and not parsed["failed"] and not parsed["answered"]
    error = {"code": "result_failed", "message": "Cannot save result.", "detail_code": "storage_error"}
    parsed = parse_call_result({"id": "call_1", "call_id": None, "status": "completed", "error": error})
    assert parsed["billing_call_id"] is None
    assert parsed["error"] == error
    assert parsed["failure_code"] == "result_failed" and parsed["failed"]


@pytest.mark.parametrize("key", ["", " ", "x" * 256, "bad\nkey", "中文"])
def test_calls_require_header_safe_stable_key(key, monkeypatch):
    def unexpected_request(*args, **kwargs):
        pytest.fail("Invalid key must not reach the network")
    monkeypatch.setattr(client.requests, "request", unexpected_request)
    with pytest.raises(CalleApiError, match="idempotency_key"):
        CalleClient(api_key="test_key").create_call({}, idempotency_key=key)


def test_calls_use_v2_and_do_not_retry_network_failure(monkeypatch):
    seen = []
    def timeout(method, url, **kwargs):
        seen.append((method, url, kwargs))
        raise client.requests.Timeout("sensitive test_key transport data")
    monkeypatch.setattr(client.requests, "request", timeout)
    with pytest.raises(CalleApiError) as caught:
        CalleClient(api_key="test_key").create_call({"phone": "+15555550123"}, idempotency_key="order-1")
    assert len(seen) == 1
    assert seen[0][0:2] == ("POST", "https://api.heycall-e.com/v2/calls")
    assert seen[0][2]["headers"]["Idempotency-Key"] == "order-1"
    assert seen[0][2]["allow_redirects"] is False
    assert "test_key" not in str(caught.value)


@pytest.mark.parametrize("kind", ["call", "goal"])
def test_wait_preserves_accepted_id_after_poll_failure(kind, monkeypatch):
    api = CalleClient(api_key="test_key")
    resource = {"id": "saved_id", "result_status": "pending"}
    monkeypatch.setattr(api, "create_call", lambda *a, **k: resource)
    monkeypatch.setattr(api, "create_goal_run", lambda *a, **k: resource)
    def fail(*args):
        raise CalleApiError("Read failed.")
    monkeypatch.setattr(api, "get_call", fail)
    monkeypatch.setattr(api, "get_goal_run", fail)
    monkeypatch.setattr(client.time, "sleep", lambda _: None)
    with pytest.raises(CalleApiError) as caught:
        if kind == "call":
            api.create_and_wait({}, idempotency_key="key", poll_interval_seconds=1, wait_timeout_seconds=60)
        else:
            api.create_goal_run_and_wait("goal_1", {}, "key", poll_interval_seconds=1, wait_timeout_seconds=60)
    assert caught.value.accepted_resource == resource


@pytest.mark.parametrize("kind", ["call", "goal"])
@pytest.mark.parametrize("final_status", ["available", "unavailable", "not_applicable"])
def test_both_waiters_stop_on_result_status(kind, final_status, monkeypatch):
    api = CalleClient(api_key="test_key")
    initial = {"id": "saved_id", "status": "completed", "result_status": "pending", "result": None, "error": None}
    final = {**initial, "result_status": final_status}
    monkeypatch.setattr(api, "create_call", lambda *a, **k: initial)
    monkeypatch.setattr(api, "create_goal_run", lambda *a, **k: initial)
    reads = []
    def read(*args):
        reads.append(args)
        return final
    monkeypatch.setattr(api, "get_call", read)
    monkeypatch.setattr(api, "get_goal_run", read)
    monkeypatch.setattr(client.time, "sleep", lambda _: None)
    if kind == "call":
        result, polls, timed_out = api.create_and_wait({}, idempotency_key="key", poll_interval_seconds=1, wait_timeout_seconds=60)
    else:
        result, polls, timed_out = api.create_goal_run_and_wait("goal_1", {}, "key", poll_interval_seconds=1, wait_timeout_seconds=60)
    assert result == final and polls == len(reads) == 1 and not timed_out


@pytest.mark.parametrize("kind", ["call", "goal"])
def test_wait_timeout_returns_original_resource_without_redial(kind, monkeypatch):
    api = CalleClient(api_key="test_key")
    resource = {"id": "saved_id", "result_status": "pending"}
    monkeypatch.setattr(api, "create_call", lambda *a, **k: resource)
    monkeypatch.setattr(api, "create_goal_run", lambda *a, **k: resource)
    if kind == "call":
        result, polls, timed_out = api.create_and_wait({}, idempotency_key="key", poll_interval_seconds=1, wait_timeout_seconds=0)
    else:
        result, polls, timed_out = api.create_goal_run_and_wait("goal_1", {}, "key", poll_interval_seconds=1, wait_timeout_seconds=0)
    assert result["id"] == "saved_id" and polls == 0 and timed_out


def test_goal_pagination_uses_after(monkeypatch):
    api = CalleClient(api_key="test_key")
    captured = {}
    def request(method, path, **kwargs):
        captured.update(kwargs)
        return {"data": [], "next_cursor": None}
    monkeypatch.setattr(api, "_request", request)
    api.list_goals(after="opaque==", limit=1)
    assert captured["query"] == {"after": "opaque==", "limit": 1}


@pytest.mark.parametrize("status,code", [(302, ""), (403, "forbidden"), (404, "wrong_code"), (500, "server_error")])
def test_credential_probe_rejects_redirect_and_undocumented_errors(status, code, monkeypatch):
    class Response:
        status_code = status
        text = "sensitive test_key body"
        def json(self):
            return {"error": {"code": code, "message": "sensitive test_key message"}}
    monkeypatch.setattr(client.requests, "request", lambda *a, **k: Response())
    with pytest.raises(CalleApiError) as caught:
        CalleClient(api_key="test_key").validate_credentials()
    assert "test_key" not in str(caught.value)


@pytest.mark.parametrize("url", ["file:///tmp/key", "https://user:password@example.com", "https://api.example.com?token=key", "https://api.example.com#key"])
def test_base_url_rejects_embedded_credentials_and_non_http_urls(url):
    with pytest.raises(CalleApiError):
        normalize_base_url(url)


def test_creation_does_not_mistake_billing_id_for_resource_id(monkeypatch):
    api = CalleClient(api_key="test_key")
    monkeypatch.setattr(api, "_request", lambda *a, **k: {"call_id": "billing_id"})
    with pytest.raises(CalleApiError, match="API resource id") as caught:
        api.create_call({}, idempotency_key="order-1")
    assert caught.value.accepted_resource == {"call_id": "billing_id"}


@pytest.mark.parametrize("invalid_json", [False, True])
def test_malformed_create_response_stays_an_uncertain_submission(invalid_json, monkeypatch):
    class Response:
        status_code = 202
        text = "invalid"
        def json(self):
            if invalid_json:
                raise ValueError("invalid JSON")
            return []
    requests = []
    def request(*args, **kwargs):
        requests.append(args)
        return Response()
    monkeypatch.setattr(client.requests, "request", request)
    with pytest.raises(CalleApiError, match="non-JSON|non-object"):
        CalleClient(api_key="test_key").create_call({}, idempotency_key="saved-key")
    assert len(requests) == 1
