"""Read and validate the published Goal interface used by a workflow mapping."""

import json
import math
import re
from typing import Any


SCALAR_TYPES = {"string", "integer", "number", "boolean"}
ROOT_KEYS = {
    "$schema",
    "type",
    "description",
    "properties",
    "required",
    "additionalProperties",
}
PROPERTY_KEYS = {"type", "description", "enum"}
INPUT_PROPERTY_KEYS = PROPERTY_KEYS | {"default", "x-calle-source"}


class GoalConfigurationError(ValueError):
    def __init__(self, code: str, path: str, message: str):
        self.finding = {"code": code, "path": path, "message": message}
        super().__init__(message)


def _object(value: Any, path: str) -> dict[str, Any]:
    def pairs(items):
        result = {}
        for name, item in items:
            if name in result:
                raise ValueError("Duplicate JSON keys are not supported.")
            result[name] = item
        return result

    def constant(_value):
        raise ValueError("Non-finite JSON numbers are not supported.")

    if value is None or value == "":
        return {}
    if isinstance(value, str):
        try:
            value = json.loads(value, object_pairs_hook=pairs, parse_constant=constant)
        except ValueError as error:
            raise GoalConfigurationError(
                "invalid_json",
                path,
                "Provide one JSON object with distinct keys and finite numbers.",
            ) from error
    if not isinstance(value, dict):
        raise GoalConfigurationError("invalid_object", path, "Provide one JSON object.")
    return value


def _matches(value: Any, kind: str | list[str]) -> bool:
    if isinstance(kind, list):
        return any(
            value is None if item == "null" else _matches(value, item) for item in kind
        )
    if kind == "string":
        return isinstance(value, str)
    if kind == "boolean":
        return isinstance(value, bool)
    if kind == "integer":
        return type(value) is int
    if kind == "number":
        return type(value) in (int, float) and (
            type(value) is int or math.isfinite(value)
        )
    return False


def _same_value(left: Any, right: Any) -> bool:
    if type(left) in (int, float) and type(right) in (int, float):
        return left == right
    return type(left) is type(right) and left == right


def _schema(value: Any, path: str, *, result: bool = False) -> dict[str, Any]:
    def reject(message):
        raise GoalConfigurationError("unsupported_goal_interface", path, message)

    if not isinstance(value, dict):
        reject("The published interface must contain an object schema.")
    unknown = sorted(set(value) - ROOT_KEYS)
    if unknown:
        reject(
            "Unsupported schema keywords: "
            + ", ".join(unknown)
            + ". Review this interface before updating the plugin."
        )
    if value.get("$schema") not in (
        None,
        "https://json-schema.org/draft/2020-12/schema",
    ):
        reject("The published interface uses an unsupported JSON Schema dialect.")
    if value.get("type") != "object" or value.get("additionalProperties") is not False:
        reject("The published schema must be a closed object.")
    if "description" in value and (
        not isinstance(value["description"], str) or len(value["description"]) > 512
    ):
        reject("The schema description must contain at most 512 characters.")
    properties = value.get("properties")
    if not isinstance(properties, dict) or len(properties) > 32:
        reject("The published schema must declare at most 32 scalar fields.")
    required = value.get("required", [])
    if not isinstance(required, list) or any(
        not isinstance(name, str) for name in required
    ):
        reject("Schema required must list declared field names.")
    if len(set(required)) != len(required) or set(required) - set(properties):
        reject("Schema required must contain distinct declared field names.")
    for name, prop in properties.items():
        if not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", name):
            reject("The published schema has an unsupported field name.")
        if not isinstance(prop, dict) or set(prop) - (
            PROPERTY_KEYS if result else INPUT_PROPERTY_KEYS
        ):
            reject(
                f"{name}: unsupported field schema keywords. Review this interface before updating the plugin."
            )
        kind = prop.get("type")
        nullable = (
            result and isinstance(kind, list) and len(kind) == 2 and "null" in kind
        )
        if nullable:
            valid_type = (
                all(isinstance(item, str) for item in kind)
                and len(set(kind)) == 2
                and set(kind) - {"null"} <= SCALAR_TYPES
            )
        else:
            valid_type = isinstance(kind, str) and kind in SCALAR_TYPES
        if not valid_type:
            reject(
                f"{name}: only scalar input fields and nullable scalar result fields are supported."
            )
        if "description" in prop and (
            not isinstance(prop["description"], str) or len(prop["description"]) > 512
        ):
            reject(f"{name}: the description must contain at most 512 characters.")
        if "default" in prop and (result or not _matches(prop["default"], kind)):
            reject(f"{name}: the default does not match its input type.")
        if not result and name not in required and "default" not in prop:
            reject(f"{name}: an optional input must declare a default.")
        if "x-calle-source" in prop and (
            not isinstance(prop["x-calle-source"], str)
            or not prop["x-calle-source"].strip()
            or "default" in prop
        ):
            reject(f"{name}: invalid source binding in the published interface.")
        if "enum" in prop:
            values = prop["enum"]
            if (
                not isinstance(values, list)
                or not 1 <= len(values) <= 32
                or any(not _matches(item, kind) for item in values)
            ):
                reject(f"{name}: enum values must match the published scalar type.")
            if any(
                _same_value(item, previous)
                for index, item in enumerate(values)
                for previous in values[:index]
            ):
                reject(f"{name}: enum values must be distinct.")
    try:
        size = len(
            json.dumps(
                value, ensure_ascii=False, allow_nan=False, separators=(",", ":")
            ).encode()
        )
    except (ValueError, TypeError) as error:
        raise GoalConfigurationError(
            "unsupported_goal_interface",
            path,
            "The published schema contains invalid JSON values.",
        ) from error
    if size > 32768:
        reject("The published schema exceeds the supported 32 KiB profile.")
    return value


def goal_interface(value: Any, goal_id: str | None = None) -> dict[str, Any]:
    goal = _object(value, "goal")
    spec = goal.get("published_run_spec")
    if (
        goal.get("object") != "goal"
        or goal.get("status") != "active"
        or not isinstance(goal.get("id"), str)
        or not goal["id"]
    ):
        raise GoalConfigurationError(
            "unsupported_goal_interface",
            "goal",
            "Select an active published Goal returned by the Goal API.",
        )
    if goal_id is not None and goal["id"] != goal_id:
        raise GoalConfigurationError(
            "goal_mismatch",
            "goal.id",
            "The Goal response does not match the selected Goal.",
        )
    if (
        not isinstance(spec, dict)
        or not isinstance(spec.get("id"), str)
        or not spec["id"]
        or type(spec.get("version")) is not int
        or spec["version"] < 1
    ):
        raise GoalConfigurationError(
            "unsupported_goal_interface",
            "published_run_spec",
            "The Goal must expose its immutable published RunSpec ID and positive integer version.",
        )
    _schema(spec.get("input_schema"), "input_schema")
    _schema(spec.get("result_schema"), "result_schema", result=True)
    return goal


def _fields(schema: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "name": name,
            "type": " | ".join(prop["type"])
            if isinstance(prop["type"], list)
            else prop["type"],
            "required": name in schema.get("required", []),
            "description": prop.get("description", ""),
            "default_json": json.dumps(prop["default"], ensure_ascii=False)
            if "default" in prop
            else "",
            "enum_json": json.dumps(prop["enum"], ensure_ascii=False)
            if "enum" in prop
            else "",
        }
        for name, prop in schema["properties"].items()
    ]


def _changes(before: dict[str, Any], after: dict[str, Any]) -> list[dict[str, str]]:
    changes = []
    if before["id"] != after["id"]:
        changes.append(
            {
                "path": "goal.id",
                "message": "The reviewed interface belongs to a different Goal.",
            }
        )
    old, new = before["published_run_spec"], after["published_run_spec"]
    if old["id"] != new["id"] or old["version"] != new["version"]:
        changes.append(
            {
                "path": "published_run_spec",
                "message": f"Published RunSpec changed from {old['id']} (version {old['version']}) to {new['id']} (version {new['version']}).",
            }
        )
    for section in ("input_schema", "result_schema"):
        previous, current = old[section], new[section]
        for name in sorted(set(previous["properties"]) | set(current["properties"])):
            left, right = (
                previous["properties"].get(name),
                current["properties"].get(name),
            )
            if json.dumps(left, sort_keys=True) != json.dumps(
                right, sort_keys=True
            ) or (
                (name in previous.get("required", []))
                != (name in current.get("required", []))
            ):
                change = (
                    "added"
                    if left is None
                    else "removed"
                    if right is None
                    else "changed"
                )
                changes.append(
                    {
                        "path": f"{section}.{name}",
                        "message": f"Field {change}; review its mapping, type, required flag, default, and enum.",
                    }
                )
    return changes


def prepare_configuration(
    goal: dict[str, Any], variables: Any, reviewed: Any
) -> dict[str, Any]:
    goal = goal_interface(goal)
    spec = goal["published_run_spec"]
    errors = []
    changes = []
    effective = {}
    try:
        submitted = _object(variables, "variables_json")
        schema = spec["input_schema"]
        properties = schema["properties"]
        effective = {
            name: prop["default"]
            for name, prop in properties.items()
            if "default" in prop
        }
        effective.update(submitted)
        for name in sorted(set(submitted) - set(properties)):
            errors.append(
                {
                    "code": "unknown_field",
                    "path": f"variables.{name}",
                    "message": "This field is not declared by the published Goal.",
                }
            )
        for name in sorted(set(schema.get("required", [])) - set(effective)):
            errors.append(
                {
                    "code": "missing_required",
                    "path": f"variables.{name}",
                    "message": "Map this required field from your upstream business record.",
                }
            )
        for name in properties.keys() & effective.keys():
            value, prop = effective[name], properties[name]
            if not _matches(value, prop["type"]):
                errors.append(
                    {
                        "code": "type_mismatch",
                        "path": f"variables.{name}",
                        "message": f"Expected a finite {prop['type']} value.",
                    }
                )
            elif "enum" in prop and not any(
                _same_value(value, option) for option in prop["enum"]
            ):
                errors.append(
                    {
                        "code": "enum_mismatch",
                        "path": f"variables.{name}",
                        "message": "The value is not a published enum option.",
                    }
                )
    except GoalConfigurationError as error:
        errors.append(error.finding)
    if reviewed is None or reviewed == "":
        errors.append(
            {
                "code": "review_required",
                "path": "reviewed_goal_json",
                "message": "Review the fields below, then save goal_json in Reviewed Goal interface. Keep that saved value until you review a new publication.",
            }
        )
    else:
        try:
            baseline = goal_interface(reviewed)
            changes = _changes(baseline, goal)
            if changes:
                errors.append(
                    {
                        "code": "goal_interface_changed",
                        "path": "reviewed_goal_json",
                        "message": "The published Goal differs from the reviewed interface. Review the affected input and result mappings before starting a new business event.",
                    }
                )
        except GoalConfigurationError as error:
            errors.append(
                {**error.finding, "path": "reviewed_goal_json." + error.finding["path"]}
            )
    ready = not errors
    result = {
        "ready": ready,
        # Empty routing outputs also stop an accidentally ungated creation node.
        "goal_id": goal["id"] if ready else "",
        "run_spec_id": spec["id"],
        "run_spec_version": spec["version"],
        "goal_json": json.dumps(goal, ensure_ascii=False, sort_keys=True),
        "input_fields": _fields(spec["input_schema"]),
        "result_fields": _fields(spec["result_schema"]),
        "variables_json": json.dumps(
            effective, ensure_ascii=False, sort_keys=True, allow_nan=False
        )
        if ready
        else "",
        "errors": errors,
        "changes": changes,
    }
    lines = [
        f"Goal: {goal.get('title') or goal['id']} ({goal['id']})",
        f"Published RunSpec: {spec['id']}, version {spec['version']}",
    ]
    for heading, field_list in (
        ("Inputs", result["input_fields"]),
        ("Results", result["result_fields"]),
    ):
        lines.append(heading + ":")
        for field in field_list:
            label = "required" if field["required"] else "optional"
            detail = (
                f"- {field['name']}: {field['type']}, {label}. {field['description']}"
            )
            if field["default_json"]:
                detail += " Default: " + field["default_json"]
            if field["enum_json"]:
                detail += " Enum: " + field["enum_json"]
            lines.append(detail)
    lines.append(
        "Ready for a new Goal Run."
        if ready
        else "Configuration needs attention; no Goal Run was created."
    )
    lines.extend(f"- {item['path']}: {item['message']}" for item in changes + errors)
    result["report"] = "\n".join(lines)
    return result
