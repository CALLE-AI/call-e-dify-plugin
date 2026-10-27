import copy
import json

import pytest

from tools.create_goal_run import CreateGoalRunTool
from tools.create_goal_run_and_wait import CreateGoalRunAndWaitTool
from tools.prepare_goal_run import PrepareGoalRunTool
from utils.client import CalleApiError, CalleClient
from utils.goal_configuration import (
    GoalConfigurationError,
    goal_interface,
    prepare_configuration,
)


@pytest.fixture
def goal():
    return {
        "object": "goal",
        "id": "goal_example",
        "title": "Reference confirmation",
        "description": "Confirm one supplied business reference.",
        "status": "active",
        "published_run_spec": {
            "id": "rspec_example_v1",
            "version": 1,
            "input_schema": {
                "type": "object",
                "additionalProperties": False,
                "required": ["reference"],
                "properties": {
                    "reference": {
                        "type": "string",
                        "description": "Map the upstream record reference.",
                    },
                    "notify": {"type": "boolean", "default": False},
                    "attempt": {"type": "integer", "default": 0},
                    "score": {"type": "number", "default": 1, "enum": [1, 2]},
                },
            },
            "result_schema": {
                "type": "object",
                "additionalProperties": False,
                "required": ["decision"],
                "properties": {
                    "decision": {
                        "type": ["null", "string"],
                        "enum": [None, "confirmed", "unknown"],
                    }
                },
            },
        },
    }


def invoke(tool_class, parameters):
    tool = tool_class.from_credentials({"api_key": "test_key"})
    return [message.model_dump(mode="json") for message in tool.invoke(parameters)]


def output(messages):
    return next(
        message["message"]["json_object"]
        for message in messages
        if message["type"] == "json"
    )


def test_preparation_preserves_values_merges_defaults_and_does_not_mutate_inputs(goal):
    original = copy.deepcopy(goal)
    variables = {
        "reference": "+14155550100",
        "notify": False,
        "attempt": 0,
        "score": 1.0,
    }
    result = prepare_configuration(goal, variables, json.dumps(goal))
    assert result["ready"] and result["goal_id"] == goal["id"]
    assert json.loads(result["variables_json"]) == variables
    assert type(json.loads(result["variables_json"])["notify"]) is bool
    assert type(json.loads(result["variables_json"])["attempt"]) is int
    assert goal == original and variables["attempt"] == 0
    assert variables["reference"] not in result["report"]
    fields = {field["name"]: field for field in result["input_fields"]}
    assert fields["reference"]["required"]
    assert fields["notify"]["default_json"] == "false"
    assert fields["attempt"]["default_json"] == "0"
    assert result["result_fields"][0]["type"] == "null | string"
    assert (
        result["run_spec_id"] == "rspec_example_v1" and result["run_spec_version"] == 1
    )


def test_published_defaults_are_applied_before_required_checks(goal):
    schema = goal["published_run_spec"]["input_schema"]
    schema["properties"]["reference"]["default"] = "DEFAULT-REFERENCE"
    result = prepare_configuration(goal, "{}", goal)
    assert result["ready"]
    assert json.loads(result["variables_json"]) == {
        "reference": "DEFAULT-REFERENCE",
        "notify": False,
        "attempt": 0,
        "score": 1,
    }


def test_first_read_explains_fields_without_authorizing_creation(goal):
    result = prepare_configuration(goal, {"reference": "REF-1"}, "")
    assert result["ready"] is False
    assert result["goal_id"] == result["variables_json"] == ""
    assert result["errors"][0]["code"] == "review_required"
    assert json.loads(result["goal_json"]) == goal
    assert result["input_fields"] and "Review" in result["report"]


@pytest.mark.parametrize(
    "variables,code,path",
    [
        ({}, "missing_required", "variables.reference"),
        (
            {"reference": "REF", "unexpected": "private value"},
            "unknown_field",
            "variables.unexpected",
        ),
        ({"reference": 1}, "type_mismatch", "variables.reference"),
        ({"reference": None}, "type_mismatch", "variables.reference"),
        ({"reference": ["REF"]}, "type_mismatch", "variables.reference"),
        ({"reference": {"nested": "REF"}}, "type_mismatch", "variables.reference"),
        ({"reference": "REF", "notify": 0}, "type_mismatch", "variables.notify"),
        ({"reference": "REF", "attempt": False}, "type_mismatch", "variables.attempt"),
        ({"reference": "REF", "attempt": 1.0}, "type_mismatch", "variables.attempt"),
        ({"reference": "REF", "score": True}, "type_mismatch", "variables.score"),
        ({"reference": "REF", "score": 3}, "enum_mismatch", "variables.score"),
        (
            {"reference": "REF", "score": float("inf")},
            "type_mismatch",
            "variables.score",
        ),
        ('{"reference":"A","reference":"B"}', "invalid_json", "variables_json"),
        ('{"reference":"REF","score":NaN}', "invalid_json", "variables_json"),
        ('{"reference":"REF","score":1e999}', "type_mismatch", "variables.score"),
        ("[]", "invalid_object", "variables_json"),
        ("not JSON", "invalid_json", "variables_json"),
    ],
)
def test_invalid_business_mappings_are_field_errors_without_routing_outputs(
    goal, variables, code, path
):
    result = prepare_configuration(goal, variables, goal)
    assert result["ready"] is False
    assert result["goal_id"] == result["variables_json"] == ""
    assert any(
        item["code"] == code and item["path"] == path for item in result["errors"]
    )
    assert "private value" not in json.dumps(result["errors"])


@pytest.mark.parametrize(
    "change,affected",
    [
        ("publication", "published_run_spec"),
        ("input_type", "input_schema.reference"),
        ("required_added", "input_schema.new_field"),
        ("default", "input_schema.attempt"),
        ("result", "result_schema.decision"),
        ("different_goal", "goal.id"),
    ],
)
def test_changed_interfaces_name_affected_fields_and_need_explicit_review(
    goal, change, affected
):
    reviewed = copy.deepcopy(goal)
    spec = goal["published_run_spec"]
    if change == "publication":
        spec.update(id="rspec_example_v2", version=2)
    elif change == "input_type":
        spec["input_schema"]["properties"]["reference"]["type"] = "number"
    elif change == "required_added":
        spec["input_schema"]["properties"]["new_field"] = {"type": "string"}
        spec["input_schema"]["required"].append("new_field")
    elif change == "default":
        spec["input_schema"]["properties"]["attempt"]["default"] = 1
    elif change == "result":
        spec["result_schema"]["properties"]["decision"]["enum"].append("declined")
    else:
        goal["id"] = "goal_other"
    result = prepare_configuration(goal, {"reference": "REF"}, reviewed)
    assert not result["ready"] and result["goal_id"] == ""
    assert any(item["path"] == affected for item in result["changes"])
    assert any(item["code"] == "goal_interface_changed" for item in result["errors"])
    assert json.loads(result["goal_json"]) == goal
    assert reviewed != goal


@pytest.mark.parametrize("reviewed", ["[]", "{}", "not JSON", '{"id":"A","id":"B"}'])
def test_invalid_reviewed_interface_is_actionable(goal, reviewed):
    result = prepare_configuration(goal, {"reference": "REF"}, reviewed)
    assert not result["ready"]
    assert any(
        item["path"].startswith("reviewed_goal_json.") for item in result["errors"]
    )


@pytest.mark.parametrize(
    "mutate",
    [
        lambda g: g.update(object="future_goal"),
        lambda g: g.update(status="paused"),
        lambda g: g.pop("published_run_spec"),
        lambda g: g["published_run_spec"].update(version=True),
        lambda g: g["published_run_spec"]["input_schema"].update(
            additionalProperties=True
        ),
        lambda g: g["published_run_spec"]["input_schema"].update(
            format="future-profile"
        ),
        lambda g: g["published_run_spec"]["input_schema"]["properties"][
            "reference"
        ].update(type="array"),
        lambda g: g["published_run_spec"]["input_schema"]["properties"][
            "reference"
        ].update(pattern=".+"),
        lambda g: g["published_run_spec"]["input_schema"]["properties"][
            "notify"
        ].update(default=0),
        lambda g: g["published_run_spec"]["input_schema"]["properties"][
            "reference"
        ].update(description="x" * 513),
        lambda g: g["published_run_spec"]["result_schema"]["properties"][
            "decision"
        ].update(type="object"),
    ],
)
def test_unsupported_api_interfaces_fail_closed_with_an_explicit_hint(goal, mutate):
    mutate(goal)
    with pytest.raises(GoalConfigurationError) as error:
        goal_interface(goal)
    assert error.value.finding["code"] == "unsupported_goal_interface"
    assert error.value.finding["message"]


def test_selected_goal_identity_must_match_the_response(goal):
    with pytest.raises(GoalConfigurationError, match="does not match"):
        goal_interface(goal, "goal_other")


def test_prepare_tool_emits_named_outputs_and_only_reads_the_goal(goal, monkeypatch):
    reads = []
    monkeypatch.setattr(
        CalleClient, "get_goal", lambda self, goal_id: reads.append(goal_id) or goal
    )
    monkeypatch.setattr(
        CalleClient,
        "create_goal_run",
        lambda *a, **k: pytest.fail("Preparation must not create a Run"),
    )
    messages = invoke(
        PrepareGoalRunTool,
        {
            "goal_id": goal["id"],
            "variables_json": '{"reference":"REF-1"}',
            "reviewed_goal_json": json.dumps(goal),
        },
    )
    result = output(messages)
    named = {
        message["message"]["variable_name"]: message["message"]["variable_value"]
        for message in messages
        if message["type"] == "variable"
    }
    assert named == result and result["ready"]
    assert reads == [goal["id"]]


@pytest.mark.parametrize("creator", [CreateGoalRunTool, CreateGoalRunAndWaitTool])
def test_invalid_configuration_cannot_reach_an_ungated_creation_tool(
    goal, monkeypatch, creator
):
    result = prepare_configuration(goal, {}, goal)
    monkeypatch.setattr(
        CalleClient,
        "create_goal_run",
        lambda *a, **k: pytest.fail("Invalid mapping must not create a Run"),
    )
    messages = invoke(
        creator,
        {
            "goal_id": result["goal_id"],
            "variables_json": result["variables_json"],
            "phone_number": "+14155550100",
            "idempotency_key": "event-8472",
            "dry_run": False,
            "confirm_live_call": True,
        },
    )
    assert any(
        "goal_id" in item.get("message", {}).get("text", "") for item in messages
    )


def test_prepare_lookup_error_keeps_ready_false(goal, monkeypatch):
    def fail(*args):
        raise CalleApiError(
            "The selected Goal is not accessible.", error_code="not_found"
        )

    monkeypatch.setattr(CalleClient, "get_goal", fail)
    result = output(invoke(PrepareGoalRunTool, {"goal_id": goal["id"]}))
    assert not result["ready"] and result["goal_id"] == ""
    assert result["errors"][0]["code"] == "not_found"


def test_goal_selector_uses_titles_ids_versions_and_every_page(goal, monkeypatch):
    other = copy.deepcopy(goal)
    other.update(id="goal_other", title=None)
    reads = []

    def read(self, *, after, limit):
        reads.append((after, limit))
        return (
            {"data": [goal], "next_cursor": "opaque-page"}
            if after is None
            else {"data": [other], "next_cursor": None}
        )

    monkeypatch.setattr(CalleClient, "list_goals", read)
    tool = PrepareGoalRunTool.from_credentials({"api_key": "test_key"})
    options = tool.fetch_parameter_options("goal_id")
    assert [item.value for item in options] == ["goal_example", "goal_other"]
    assert (
        "Reference confirmation" in options[0].label.model_dump(by_alias=True)["en_US"]
    )
    assert "v1" in options[0].label.model_dump(by_alias=True)["en_US"]
    assert reads == [(None, 100), ("opaque-page", 100)]


def test_goal_selector_rejects_a_repeated_cursor(goal, monkeypatch):
    monkeypatch.setattr(
        CalleClient,
        "list_goals",
        lambda *a, **k: {"data": [goal], "next_cursor": "same"},
    )
    tool = PrepareGoalRunTool.from_credentials({"api_key": "test_key"})
    with pytest.raises(ValueError, match="non-progressing"):
        tool.fetch_parameter_options("goal_id")
