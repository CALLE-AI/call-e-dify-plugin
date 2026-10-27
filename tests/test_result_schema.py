import copy
import json

import pytest

from utils.client import build_create_payload, parse_result_schema, result_schema


SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "appointment_status": {"type": "string", "enum": ["confirmed", "reschedule_requested", "unknown"]},
        "preferred_time": {"type": "string", "description": "Requested time, or unknown."},
        "accepted": {"type": "boolean"},
        "party_size": {"type": "integer", "enum": [0, 2]},
        "duration": {"type": "number", "enum": [0, 0.5]},
    },
    "required": ["appointment_status"],
}


@pytest.mark.parametrize("value", [None, "", "  \n\t"])
def test_blank_schema_keeps_the_existing_request(value):
    payload = build_create_payload(phone_number="+14155550100", task="Speak English.",
                                   region="", locale="", metadata={}, schema=value)
    assert payload["result_schema"] == result_schema()
    assert "region" not in payload and "locale" not in payload


@pytest.mark.parametrize("as_json", [False, True])
def test_custom_schema_is_preserved_without_merging_defaults(as_json):
    schema = copy.deepcopy(SCHEMA)
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    value = json.dumps(schema) if as_json else schema
    parsed = parse_result_schema(value)
    assert parsed == schema
    assert "outcome" not in parsed["properties"]
    assert parsed["required"] == ["appointment_status"]


@pytest.mark.parametrize("count", [0, 32])
def test_empty_and_maximum_size_closed_objects_are_supported(count):
    schema = {"type": "object", "additionalProperties": False,
              "properties": {f"field_{i}": {"type": "string"} for i in range(count)}}
    assert parse_result_schema(schema) == schema


@pytest.mark.parametrize("value", [
    "{", "[]", "null", "true", "42", {}, [],
    '{"type":"object","type":"string"}',
    '{"type":"object","additionalProperties":false,"properties":{"a":{"type":"string"},"a":{"type":"number"}}}',
])
def test_invalid_json_and_root_shapes_are_rejected(value):
    with pytest.raises(ValueError, match="result_schema"):
        parse_result_schema(value)


@pytest.mark.parametrize("change", [
    {"additionalProperties": True}, {"additionalProperties": 0}, {"type": "array"},
    {"properties": None}, {"properties": []},
    {"properties": {f"f{i}": {"type": "string"} for i in range(33)}},
    {"required": "appointment_status"}, {"required": ["missing"]},
    {"required": ["appointment_status", "appointment_status"]}, {"required": [{}]},
    {"oneOf": []}, {"$ref": "#/other"}, {"title": "Unsupported"},
    {"description": "x" * 513}, {"description": 12},
    {"$schema": "http://json-schema.org/draft-07/schema#"},
])
def test_invalid_root_contract_is_rejected(change):
    with pytest.raises(ValueError, match="result_schema"):
        parse_result_schema({**SCHEMA, **change})


@pytest.mark.parametrize("property_schema", [
    {}, False, {"type": "object"}, {"type": "array"}, {"type": "null"},
    {"type": ["string", "null"]}, {"type": "string", "default": "unknown"},
    {"type": "string", "minLength": 1}, {"type": "string", "pattern": "yes"},
    {"type": "integer", "minimum": 0}, {"type": "string", "anyOf": []},
    {"type": "string", "description": []}, {"type": "string", "description": "x" * 513},
    {"type": "string", "enum": []}, {"type": "string", "enum": "yes"},
    {"type": "string", "enum": ["yes", "yes"]}, {"type": "string", "enum": [None]},
    {"type": "string", "enum": [{}]}, {"type": "string", "enum": list(map(str, range(33)))},
    {"type": "boolean", "enum": [1]}, {"type": "integer", "enum": [True]},
    {"type": "integer", "enum": [1.5]}, {"type": "integer", "enum": [2 ** 53]},
    {"type": "number", "enum": [float("nan")]}, {"type": "number", "enum": [float("inf")]},
    {"type": "number", "enum": [1, 1.0]},
])
def test_unsupported_property_contract_is_rejected(property_schema):
    with pytest.raises(ValueError, match="result_schema"):
        parse_result_schema({"type": "object", "additionalProperties": False,
                             "properties": {"answer": property_schema}})


@pytest.mark.parametrize("name", ["", "Upper", "a-b", "a.b", "1answer", "a" * 65, 1])
def test_invalid_field_names_are_rejected(name):
    with pytest.raises(ValueError, match="result_schema"):
        parse_result_schema({"type": "object", "additionalProperties": False,
                             "properties": {name: {"type": "string"}}})


def test_schema_byte_limit_is_checked_with_unicode():
    schema = {"type": "object", "additionalProperties": False, "properties": {
        f"field_{i}": {"type": "string", "description": "中" * 512} for i in range(32)
    }}
    with pytest.raises(ValueError, match="32 KiB"):
        parse_result_schema(schema)
