import json

import pytest
from dify_plugin.config.config import DifyPluginEnv
from dify_plugin.core.plugin_registration import PluginRegistration

from tools.create_and_wait import CreateAndWaitTool
from tools.create_call import CreateCallTool
from tools.create_goal_run import CreateGoalRunTool
from tools.create_goal_run_and_wait import CreateGoalRunAndWaitTool
from utils import client


CREATE_TOOLS = [CreateCallTool, CreateAndWaitTool, CreateGoalRunTool, CreateGoalRunAndWaitTool]
PARAMETERS = {
    "phone_number": "+14155550100",
    "task": "Speak English and confirm the test connection.",
    "goal_id": "goal_1",
    "idempotency_key": "order-8472-attempt-1",
    "variables_json": '{"order_id":"8472"}',
    "metadata_json": '{"lead_id":"lead_1"}',
}


def invoke(tool_class, parameters):
    tool = tool_class.from_credentials({"api_key": "test_key"})
    return [message.model_dump(mode="json") for message in tool.invoke(parameters)]


def json_output(messages):
    return next(message["message"]["json_object"] for message in messages if message["type"] == "json")


def test_dify_registers_all_nine_tools():
    registration = PluginRegistration(DifyPluginEnv())
    assert len(registration.tools_mapping["call_e"][2]) == 9


@pytest.mark.parametrize("tool_class", CREATE_TOOLS)
@pytest.mark.parametrize("controls", [{}, {"dry_run": False}, {"confirm_live_call": True}])
def test_live_call_requires_both_controls(tool_class, controls, monkeypatch):
    def unexpected_request(*args, **kwargs):
        pytest.fail("Preview must not access the network")
    monkeypatch.setattr(client.requests, "request", unexpected_request)
    result = json_output(invoke(tool_class, {**PARAMETERS, **controls}))
    assert result["live_call_created"] is False
    assert result["idempotency_key"] == PARAMETERS["idempotency_key"]
    assert PARAMETERS["phone_number"] not in json.dumps(result)


@pytest.mark.parametrize("tool_class", CREATE_TOOLS)
@pytest.mark.parametrize("key", [None, " ", "event\n1"])
def test_invalid_key_reports_local_configuration_error_without_submission(tool_class, key, monkeypatch):
    def unexpected_request(*args, **kwargs):
        pytest.fail("An invalid idempotency key must not access the network")
    monkeypatch.setattr(client.requests, "request", unexpected_request)
    messages = invoke(tool_class, {
        **PARAMETERS, "idempotency_key": key, "dry_run": False, "confirm_live_call": True,
    })
    result = json_output(messages)
    assert result["accepted_resource"] is None
    assert "No CALL-E request was sent" in result["error"]["message"]
    assert "Idempotency Key" in result["recovery"]
    assert "saved business event ID" in result["recovery"]
    assert "uncertain" not in result["recovery"]


@pytest.mark.parametrize("tool_class", CREATE_TOOLS)
def test_exact_replay_uses_the_supplied_key_and_body(tool_class, monkeypatch):
    calls = []
    class Response:
        status_code = 202
        text = "{}"
        def json(self):
            return {
                "id": "saved_id", "status": "completed", "result_status": "unavailable",
                "call_outcome": "no_answer", "result": None, "error": None, "transcript": [],
                "metadata": {"source": "dify_call_e_plugin", "lead_id": "lead_1"},
            }
    def request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        return Response()
    monkeypatch.setattr(client.requests, "request", request)
    parameters = {**PARAMETERS, "dry_run": False, "confirm_live_call": True}
    first = json_output(invoke(tool_class, parameters))
    replay = json_output(invoke(tool_class, parameters))
    assert len(calls) == 2 and calls[0] == calls[1]
    assert calls[0][2]["headers"]["Idempotency-Key"] == parameters["idempotency_key"]
    body = calls[0][2]["json"]
    assert body["phone"] == parameters["phone_number"]
    assert "phone_number" not in body
    if tool_class in (CreateCallTool, CreateAndWaitTool):
        assert calls[0][1].endswith("/v2/calls")
        assert "region" not in body and "locale" not in body
        assert body["result_schema"]["additionalProperties"] is False
        assert first["call_id"] == replay["call_id"] == "saved_id"
        assert first["result"]["result"] is None
        assert first["result"]["result_status"] == "unavailable"
    else:
        assert calls[0][1].endswith("/v1/goals/goal_1/runs")
        assert set(body) == {"phone", "variables"}
        assert first["goal_run_id"] == replay["goal_run_id"] == "saved_id"
    if tool_class in (CreateAndWaitTool, CreateGoalRunAndWaitTool):
        assert first["poll_count"] == 0 and not first["timed_out"]


@pytest.mark.parametrize("tool_class", CREATE_TOOLS)
def test_creation_network_error_returns_recovery_key_without_retry(tool_class, monkeypatch):
    calls = []
    def request(*args, **kwargs):
        calls.append(args)
        raise client.requests.Timeout("sensitive test_key transport detail")
    monkeypatch.setattr(client.requests, "request", request)
    messages = invoke(tool_class, {**PARAMETERS, "dry_run": False, "confirm_live_call": True})
    result = json_output(messages)
    assert len(calls) == 1
    assert result["idempotency_key"] == PARAMETERS["idempotency_key"]
    assert result["accepted_resource"] is None
    assert "test_key" not in json.dumps(messages)
    assert "original" in result["recovery"]


@pytest.mark.parametrize("tool_class", [CreateAndWaitTool, CreateGoalRunAndWaitTool])
def test_poll_error_returns_accepted_id_and_original_key(tool_class, monkeypatch):
    class Response:
        status_code = 202
        text = "{}"
        def json(self):
            return {"id": "saved_id", "result_status": "pending"}
    posts = []
    def request(method, url, **kwargs):
        if method == "POST":
            posts.append(url)
            return Response()
        raise client.requests.ConnectionError("read failed")
    monkeypatch.setattr(client.requests, "request", request)
    monkeypatch.setattr(client.time, "sleep", lambda _: None)
    result = json_output(invoke(tool_class, {**PARAMETERS, "dry_run": False, "confirm_live_call": True}))
    assert len(posts) == 1
    assert result["accepted_resource"]["id"] == "saved_id"
    assert result["idempotency_key"] == PARAMETERS["idempotency_key"]


def test_creation_error_preserves_api_reason_and_missing_inputs(monkeypatch):
    class Response:
        status_code = 422
        text = "{}"
        def json(self):
            return {"error": {"code": "input_incomplete", "message": "Missing appointment time.",
                              "details": {"missing_inputs": ["Provide the appointment time."]}}}
    monkeypatch.setattr(client.requests, "request", lambda *a, **k: Response())
    result = json_output(invoke(CreateCallTool, {**PARAMETERS, "dry_run": False, "confirm_live_call": True}))
    assert result["error"]["code"] == "input_incomplete"
    assert result["error"]["details"]["missing_inputs"] == ["Provide the appointment time."]


@pytest.mark.parametrize("tool_class", [CreateCallTool, CreateAndWaitTool])
def test_named_call_outputs_are_emitted_as_dify_variables(tool_class):
    messages = invoke(tool_class, PARAMETERS)
    payload = json_output(messages)
    variables = {
        message["message"]["variable_name"]: message["message"]["variable_value"]
        for message in messages if message["type"] == "variable"
    }
    assert variables == payload


def test_get_call_emits_v2_fields_as_named_variables(monkeypatch):
    from tools.get_call import GetCallTool

    monkeypatch.setattr(client.CalleClient, "get_call", lambda *a: {
        "id": "call_1", "call_id": None, "status": "completed", "result_status": "unavailable",
        "result": None, "error": None, "call_outcome": "busy", "transcript": [],
    })
    messages = invoke(GetCallTool, {"call_id": "call_1"})
    variables = {
        message["message"]["variable_name"]: message["message"]["variable_value"]
        for message in messages if message["type"] == "variable"
    }
    assert variables == json_output(messages)
    assert variables["call_id"] == "call_1" and variables["billing_call_id"] is None
    assert variables["result_status"] == "unavailable" and variables["result"] is None


@pytest.mark.parametrize("tool_class", [CreateCallTool, CreateAndWaitTool])
@pytest.mark.parametrize("live", [False, True])
def test_invalid_custom_schema_is_rejected_before_any_network_request(tool_class, live, monkeypatch):
    def unexpected_request(*args, **kwargs):
        pytest.fail("An invalid result schema must not access the network")
    monkeypatch.setattr(client.requests, "request", unexpected_request)
    messages = invoke(tool_class, {**PARAMETERS, "result_schema": '{"type":"object"}',
                                  "dry_run": not live, "confirm_live_call": live})
    assert any("result_schema" in message["message"].get("text", "") for message in messages)
    assert not any(message["type"] == "json" for message in messages)


@pytest.mark.parametrize("tool_class", [CreateCallTool, CreateAndWaitTool])
def test_custom_schema_preview_and_recovery_preserve_the_original_request(tool_class, monkeypatch):
    schema = {"type": "object", "additionalProperties": False, "properties": {
        "appointment_status": {"type": "string", "enum": ["confirmed", "unknown"]},
    }, "required": ["appointment_status"]}
    parameters = {**PARAMETERS, "result_schema": json.dumps(schema)}
    preview = json_output(invoke(tool_class, parameters))
    assert preview["result_schema"] == schema
    requests = []
    class Response:
        status_code = 202
        text = "{}"
        def json(self):
            return {"id": "call_saved", "status": "completed", "call_outcome": "completed",
                    "result_status": "available", "result": {"appointment_status": "unknown"}, "error": None}
    def request(method, url, **kwargs):
        requests.append((method, url, kwargs))
        if len(requests) == 1:
            raise client.requests.Timeout("Lost accepted response")
        return Response()
    monkeypatch.setattr(client.requests, "request", request)
    parameters.update(dry_run=False, confirm_live_call=True)
    uncertain = json_output(invoke(tool_class, parameters))
    recovered_messages = invoke(tool_class, parameters)
    recovered = json_output(recovered_messages)
    assert len(requests) == 2 and requests[0] == requests[1]
    assert requests[0][2]["json"]["result_schema"] == schema
    assert uncertain["accepted_resource"] is None
    assert recovered["call_id"] == "call_saved"
    assert recovered["result"]["result"] == {"appointment_status": "unknown"}
    variables = {m["message"]["variable_name"]: m["message"]["variable_value"]
                 for m in recovered_messages if m["type"] == "variable"}
    assert variables["result"] == recovered["result"]


@pytest.mark.parametrize("result,status,outcome", [
    ({"appointment_status": "unknown", "accepted": False, "party_size": 0}, "available", "completed"),
    ({}, "available", "completed"),
    (None, "unavailable", "no_answer"),
])
def test_get_call_preserves_custom_fields_and_unavailable_results(result, status, outcome, monkeypatch):
    from tools.get_call import GetCallTool

    monkeypatch.setattr(client.CalleClient, "get_call", lambda *a: {
        "id": "call_1", "status": "completed", "call_outcome": outcome,
        "result_status": status, "result": result, "error": None,
    })
    output = json_output(invoke(GetCallTool, {"call_id": "call_1"}))
    assert output["result"] == output["structured_result"] == result
    assert output["result_status"] == status and output["call_outcome"] == outcome
    assert output["error"] is None and output["summary"] is None
