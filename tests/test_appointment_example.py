from pathlib import Path

import pytest
import yaml

from utils.client import parse_result_schema


EXAMPLE = yaml.safe_load((Path(__file__).resolve().parents[1] / "examples/appointment-confirmation.yml").read_text())
NODES = {node["id"]: node["data"] for node in EXAMPLE["workflow"]["graph"]["nodes"]}


def branch(call):
    namespace = {}
    exec(NODES["fields"]["code"], namespace)
    return namespace["main"]([{"result": call}])


def test_example_uses_custom_fields_and_starts_without_dialing():
    parameters = NODES["call"]["tool_parameters"]
    schema = parse_result_schema(parameters["result_schema"]["value"])
    assert schema["required"] == ["appointment_status", "preferred_time"]
    assert parameters["dry_run"]["value"] is True
    assert parameters["confirm_live_call"]["value"] is False
    assert parameters["idempotency_key"]["value"] == "{{#start.call_request_id#}}"


@pytest.mark.parametrize("status", ["confirmed", "reschedule_requested", "unknown"])
def test_example_reads_business_fields_and_selects_the_matching_branch(status):
    output = branch({"result_status": "available", "call_outcome": "completed", "error": None,
                     "result": {"appointment_status": status, "preferred_time": "Tuesday at 10"}})
    assert output["appointment_status"] == status
    assert output["preferred_time"] == "Tuesday at 10"
    assert output["route"] == (status if status != "unknown" else "manual_review")
    cases = {case["case_id"] for case in NODES["route"]["cases"]}
    assert (output["route"] in cases) == (status != "unknown")


@pytest.mark.parametrize("call", [
    None, {},
    {"result_status": "available", "call_outcome": "completed", "result": {}},
    {"result_status": "unavailable", "call_outcome": "completed", "result": None, "error": None},
    {"result_status": "pending", "call_outcome": "completed", "result": {"appointment_status": "confirmed"}},
    {"result_status": "available", "call_outcome": "failed", "result": {"appointment_status": "confirmed"}},
    {"result_status": "available", "call_outcome": "completed", "error": {"code": "failed"},
     "result": {"appointment_status": "confirmed"}},
    {"result_status": "available", "call_outcome": "completed", "result": {"appointment_status": ["confirmed"]}},
])
def test_example_never_treats_missing_or_uncertain_evidence_as_confirmation(call):
    assert branch(call)["route"] == "manual_review"
