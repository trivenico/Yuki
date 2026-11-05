"""Bounded, dependency-free JSON contracts for specialist-agent boundaries.

This validator intentionally implements a documented JSON Schema subset. It
never executes schema expressions, custom formats, remote references or code.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import math
from typing import Any

_MAX_DOCUMENT_BYTES = 64 * 1024
_MAX_DEPTH = 16
_MAX_ISSUES = 100
_TYPES = frozenset({"object", "array", "string", "integer", "number", "boolean", "null"})
_SCHEMA_KEYS = frozenset({
    "type", "properties", "required", "additionalProperties", "items", "enum",
    "minLength", "maxLength", "minItems", "maxItems", "minimum", "maximum",
})


@dataclass(frozen=True)
class ContractIssue:
    path: str
    code: str


@dataclass(frozen=True)
class AgentContract:
    version: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]


def _json_bytes(value: Any) -> bytes:
    try:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise ValueError("contract_payload_not_json") from exc
    if len(encoded) > _MAX_DOCUMENT_BYTES:
        raise ValueError("contract_payload_too_large")
    return encoded


def _check_depth(value: Any, depth: int = 0) -> None:
    if depth > _MAX_DEPTH:
        raise ValueError("contract_payload_too_deep")
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("contract_object_keys_must_be_strings")
            _check_depth(item, depth + 1)
    elif isinstance(value, list):
        for item in value:
            _check_depth(item, depth + 1)


def _validate_schema(schema: Any, depth: int = 0) -> None:
    if depth > _MAX_DEPTH or not isinstance(schema, dict):
        raise ValueError("contract_schema_invalid")
    if set(schema) - _SCHEMA_KEYS:
        raise ValueError("contract_schema_unsupported_keyword")
    kind = schema.get("type")
    if not isinstance(kind, str) or kind not in _TYPES:
        raise ValueError("contract_schema_type_invalid")
    if "enum" in schema:
        values = schema["enum"]
        if not isinstance(values, list) or not values or len(values) > 100:
            raise ValueError("contract_schema_enum_invalid")
        _json_bytes(values)
    for key in ("minLength", "maxLength", "minItems", "maxItems"):
        if key in schema and (type(schema[key]) is not int or schema[key] < 0 or schema[key] > 100000):
            raise ValueError("contract_schema_bound_invalid")
    for low, high in (("minLength", "maxLength"), ("minItems", "maxItems")):
        if low in schema and high in schema and schema[low] > schema[high]:
            raise ValueError("contract_schema_bounds_inverted")
    for key in ("minimum", "maximum"):
        if key in schema and (type(schema[key]) not in (int, float) or not math.isfinite(schema[key])):
            raise ValueError("contract_schema_numeric_bound_invalid")
    if "minimum" in schema and "maximum" in schema and schema["minimum"] > schema["maximum"]:
        raise ValueError("contract_schema_bounds_inverted")
    if kind == "object":
        properties = schema.get("properties", {})
        required = schema.get("required", [])
        if not isinstance(properties, dict) or len(properties) > 100 or any(not isinstance(key, str) for key in properties):
            raise ValueError("contract_schema_properties_invalid")
        if not isinstance(required, list) or len(required) > 100 or any(not isinstance(k, str) for k in required):
            raise ValueError("contract_schema_required_invalid")
        if len(set(required)) != len(required) or any(k not in properties for k in required):
            raise ValueError("contract_schema_required_invalid")
        if type(schema.get("additionalProperties", True)) is not bool:
            raise ValueError("contract_schema_additional_properties_invalid")
        for child in properties.values():
            _validate_schema(child, depth + 1)
    elif "properties" in schema or "required" in schema or "additionalProperties" in schema:
        raise ValueError("contract_schema_keyword_type_mismatch")
    if kind == "array" and "items" in schema:
        _validate_schema(schema["items"], depth + 1)
    elif "items" in schema:
        raise ValueError("contract_schema_keyword_type_mismatch")
    if kind not in {"string"} and any(k in schema for k in ("minLength", "maxLength")):
        raise ValueError("contract_schema_keyword_type_mismatch")
    if kind != "array" and any(k in schema for k in ("minItems", "maxItems")):
        raise ValueError("contract_schema_keyword_type_mismatch")
    if kind not in {"integer", "number"} and any(k in schema for k in ("minimum", "maximum")):
        raise ValueError("contract_schema_keyword_type_mismatch")


def _matches_type(value: Any, kind: str) -> bool:
    if kind == "object":
        return isinstance(value, dict)
    if kind == "array":
        return isinstance(value, list)
    if kind == "string":
        return isinstance(value, str)
    if kind == "integer":
        return type(value) is int
    if kind == "number":
        return type(value) in (int, float) and math.isfinite(value)
    if kind == "boolean":
        return type(value) is bool
    return value is None


def validate_contract(schema: dict[str, Any], payload: Any) -> tuple[ContractIssue, ...]:
    """Validate JSON data against the supported schema subset; values are never echoed."""
    _validate_schema(schema)
    _json_bytes(payload)
    _check_depth(payload)
    issues: list[ContractIssue] = []

    def add(path: str, code: str) -> None:
        if len(issues) < _MAX_ISSUES:
            issues.append(ContractIssue(path=path, code=code))

    def visit(current_schema: dict[str, Any], value: Any, path: str, depth: int) -> None:
        if len(issues) >= _MAX_ISSUES:
            return
        kind = current_schema["type"]
        if not _matches_type(value, kind):
            add(path, "type_mismatch")
            return
        if "enum" in current_schema and value not in current_schema["enum"]:
            add(path, "enum_mismatch")
        if kind == "object":
            properties = current_schema.get("properties", {})
            for key in current_schema.get("required", []):
                if key not in value:
                    add(path + "/" + key.replace("~", "~0").replace("/", "~1"), "required")
            if current_schema.get("additionalProperties", True) is False:
                for key in value.keys() - properties.keys():
                    add(path + "/" + key.replace("~", "~0").replace("/", "~1"), "additional_property")
            for key, child in properties.items():
                if key in value:
                    visit(child, value[key], path + "/" + key.replace("~", "~0").replace("/", "~1"), depth + 1)
        elif kind == "array":
            if len(value) < current_schema.get("minItems", 0):
                add(path, "min_items")
            if len(value) > current_schema.get("maxItems", 100000):
                add(path, "max_items")
            if "items" in current_schema:
                for index, item in enumerate(value):
                    visit(current_schema["items"], item, path + "/" + str(index), depth + 1)
                    if len(issues) >= _MAX_ISSUES:
                        break
        elif kind == "string":
            if len(value) < current_schema.get("minLength", 0):
                add(path, "min_length")
            if len(value) > current_schema.get("maxLength", 100000):
                add(path, "max_length")
        elif kind in {"integer", "number"}:
            if "minimum" in current_schema and value < current_schema["minimum"]:
                add(path, "minimum")
            if "maximum" in current_schema and value > current_schema["maximum"]:
                add(path, "maximum")

    visit(schema, payload, "$", 0)
    return tuple(issues)


def validate_agent_input(contract: AgentContract, payload: Any) -> tuple[ContractIssue, ...]:
    if not isinstance(contract, AgentContract):
        raise TypeError("agent_contract_required")
    return validate_contract(contract.input_schema, payload)


def validate_agent_output(contract: AgentContract, payload: Any) -> tuple[ContractIssue, ...]:
    if not isinstance(contract, AgentContract):
        raise TypeError("agent_contract_required")
    return validate_contract(contract.output_schema, payload)


__all__ = [
    "AgentContract", "ContractIssue", "validate_contract",
    "validate_agent_input", "validate_agent_output",
]
