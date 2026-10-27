import copy
import json
from pathlib import Path

import pytest
import yaml

from tools.create_goal_run_and_wait import CreateGoalRunAndWaitTool
from utils.client import CalleClient


EXAMPLE = yaml.safe_load(
    (
        Path(__file__).resolve().parents[1] / "examples/goal-run-configuration.yml"
    ).read_text()
)
NODES = {node["id"]: node["data"] for node in EXAMPLE["workflow"]["graph"]["nodes"]}


def run_code(name, *args):
    namespace = {}
    exec(NODES[name]["code"], namespace)
    return namespace["main"](*args)


def test_mapping_uses_saved_business_identity_and_declared_goal_fields():
    record = {
        "reference": "ORDER-8472",
        "phone_number": "+14155550100",
        "event_id": "order-8472:confirm:1",
    }
    first = run_code("map", json.dumps(record))
    assert first == run_code("map", json.dumps(record))
    assert json.loads(first["variables_json"]) == {
        "test_reference": record["reference"]
    }
    assert first["idempotency_key"] == record["event_id"]
    assert first["phone_number"] == record["phone_number"]


@pytest.mark.parametrize("field", ["reference", "phone_number", "event_id"])
def test_mapping_rejects_missing_business_fields(field):
    record = {
        "reference": "ORDER-8472",
        "phone_number": "+14155550100",
        "event_id": "event-8472",
    }
    del record[field]
    with pytest.raises(ValueError, match=field):
        run_code("map", json.dumps(record))


def test_graph_gates_creation_and_defaults_to_preview():
    edges = EXAMPLE["workflow"]["graph"]["edges"]
    assert (
        next(edge for edge in edges if edge["target"] == "run")["sourceHandle"]
        == "ready"
    )
    assert NODES["gate"]["cases"][0]["conditions"][0]["variable_selector"] == [
        "prepare",
        "ready",
    ]
    parameters = NODES["run"]["tool_parameters"]
    assert parameters["goal_id"]["value"] == ["prepare", "goal_id"]
    assert parameters["variables_json"]["value"] == ["prepare", "variables_json"]
    assert parameters["dry_run"]["value"] is True
    assert parameters["confirm_live_call"]["value"] is False
    assert (
        NODES["prepare"]["tool_configurations"]["reviewed_goal_json"]["type"]
        == "constant"
    )


def accepted():
    return {
        "goal_run_id": "rgrp_synthetic_8472",
        "timed_out": False,
        "result": {"confirmed": "yes"},
        "goal_run": {
            "object": "goal_run",
            "id": "rgrp_synthetic_8472",
            "run_spec": {"id": "rspec_example_v1", "version": 1},
            "result_status": "available",
            "call_outcome": "completed",
            "result": {"confirmed": "yes"},
            "error": None,
        },
    }


def test_explicit_available_confirmation_and_matching_interface_select_confirmation():
    result = run_code("inspect", [accepted()], "rspec_example_v1", 1)
    assert (
        result["route"] == "confirmed"
        and result["goal_run_id"] == "rgrp_synthetic_8472"
    )


def test_inspection_consumes_the_execution_tools_actual_output(monkeypatch):
    resource = accepted()["goal_run"]
    monkeypatch.setattr(
        CalleClient,
        "create_goal_run_and_wait",
        lambda *args, **kwargs: (resource, 1, False),
    )
    tool = CreateGoalRunAndWaitTool.from_credentials({"api_key": "test_key"})
    messages = list(
        tool.invoke(
            {
                "goal_id": "goal_example",
                "phone_number": "+14155550100",
                "variables_json": '{"test_reference":"event-8472"}',
                "idempotency_key": "event-8472",
                "dry_run": False,
                "confirm_live_call": True,
            }
        )
    )
    output = [
        message.message.json_object
        for message in messages
        if message.type.value == "json"
    ]
    result = run_code("inspect", output, "rspec_example_v1", 1)
    assert result["route"] == "confirmed"
    assert json.loads(result["business_result_json"]) == {"confirmed": "yes"}


@pytest.mark.parametrize(
    "mutate",
    [
        lambda r: r["goal_run"].update(result=None),
        lambda r: r["goal_run"].update(result={}),
        lambda r: r["goal_run"].update(result={"confirmed": None}),
        lambda r: r["goal_run"].update(result={"confirmed": "unknown"}),
        lambda r: r["goal_run"].update(result={"confirmed": True}),
        lambda r: r["goal_run"].update(result_status="unavailable"),
        lambda r: r["goal_run"].update(result_status="pending"),
        lambda r: r["goal_run"].update(call_outcome="failed"),
        lambda r: r["goal_run"].update(error={}),
        lambda r: r.update(error={"code": "lookup_failed"}),
        lambda r: r.update(timed_out=True),
    ],
)
def test_uncertain_results_do_not_confirm_business_success(mutate):
    response = accepted()
    mutate(response)
    assert (
        run_code("inspect", [response], "rspec_example_v1", 1)["route"]
        == "manual_review"
    )


def test_publication_between_preparation_and_acceptance_preserves_the_accepted_id():
    response = accepted()
    response["goal_run"]["run_spec"] = {"id": "rspec_example_v2", "version": 2}
    result = run_code("inspect", [response], "rspec_example_v1", 1)
    assert result["route"] == "interface_changed_after_acceptance"
    assert result["goal_run_id"] == "rgrp_synthetic_8472"
    assert not any(
        edge["target"] == "run" and edge["source"] in {"inspect", "end"}
        for edge in EXAMPLE["workflow"]["graph"]["edges"]
    )


def test_poll_failure_retains_the_accepted_run_for_recovery():
    response = accepted()
    raw = response.pop("goal_run")
    response.update(accepted_resource=raw, error={"code": "lookup_failed"})
    result = run_code("inspect", [response], "rspec_example_v1", 1)
    assert result["route"] == "manual_review" and result["goal_run_id"] == raw["id"]


def test_preview_is_not_a_confirmed_goal_execution():
    result = run_code("inspect", [{"dry_run": True}], "rspec_example_v1", 1)
    assert result["route"] == "preview" and result["goal_run_id"] == ""


def test_inspection_does_not_mutate_tool_results():
    response = accepted()
    before = copy.deepcopy(response)
    run_code("inspect", [response], "rspec_example_v1", 1)
    assert response == before
