"""Strict, dependency-free validation for model-visible function tool contracts."""
from __future__ import annotations

import math
import re
from collections.abc import Mapping
from typing import Any


_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,127}$")
_SCHEMA_KEYS = frozenset({
    "type", "description", "title", "properties", "required",
    "additionalProperties", "items", "enum", "minimum", "maximum",
    "minLength", "maxLength", "minItems", "maxItems",
})
_TYPES = frozenset({"object", "array", "string", "integer", "number", "boolean", "null"})
_MAX_SCHEMA_DEPTH = 8
_MAX_PROPERTIES = 64
_MAX_ENUM_VALUES = 100


class ToolSchemaError(ValueError):
    """Raised when a tool definition or model-proposed arguments violate its schema."""


def _bounded_integer(value: Any, label: str, maximum: int = 100_000) -> int:
    if type(value) is not int or not 0 <= value <= maximum:
        raise ToolSchemaError(f"{label} must be an integer between 0 and {maximum}.")
    return value


def _validate_schema(schema: Any, *, depth: int = 0) -> dict[str, Any]:
    if depth > _MAX_SCHEMA_DEPTH or not isinstance(schema, Mapping):
        raise ToolSchemaError("tool parameter schema is invalid or too deeply nested.")
    if set(schema) - _SCHEMA_KEYS:
        raise ToolSchemaError("tool parameter schema contains unsupported keywords.")
    kind = schema.get("type")
    if kind not in _TYPES:
        raise ToolSchemaError("tool parameter schema requires a supported type.")
    normalized = dict(schema)
    for field in ("description", "title"):
        if field in schema and (not isinstance(schema[field], str) or len(schema[field]) > 1024):
            raise ToolSchemaError(f"schema {field} must be bounded text.")
    if "enum" in schema:
        values = schema["enum"]
        if not isinstance(values, list) or not 1 <= len(values) <= _MAX_ENUM_VALUES:
            raise ToolSchemaError("schema enum must contain 1 to 100 values.")
        normalized["enum"] = values
    if kind == "object":
        properties = schema.get("properties", {})
        required = schema.get("required", [])
        additional = schema.get("additionalProperties", True)
        if not isinstance(properties, Mapping) or len(properties) > _MAX_PROPERTIES:
            raise ToolSchemaError("object properties must be a bounded mapping.")
        if not isinstance(required, list) or len(required) > _MAX_PROPERTIES:
            raise ToolSchemaError("object required must be a bounded list.")
        if any(not isinstance(key, str) or not _NAME.fullmatch(key) for key in properties):
            raise ToolSchemaError("object property names must be valid identifiers.")
        if any(not isinstance(key, str) or key not in properties for key in required) or len(set(required)) != len(required):
            raise ToolSchemaError("object required entries must be unique declared properties.")
        if type(additional) is not bool:
            raise ToolSchemaError("additionalProperties must be a boolean.")
        normalized["properties"] = {
            key: _validate_schema(value, depth=depth + 1)
            for key, value in properties.items()
        }
        normalized["required"] = list(required)
        normalized["additionalProperties"] = additional
    elif "properties" in schema or "required" in schema or "additionalProperties" in schema:
        raise ToolSchemaError("object-only schema keywords require type=object.")
    if kind == "array":
        if "items" in schema:
            normalized["items"] = _validate_schema(schema["items"], depth=depth + 1)
        elif "items" not in schema and ("minItems" in schema or "maxItems" in schema):
            pass
    elif "items" in schema or "minItems" in schema or "maxItems" in schema:
        raise ToolSchemaError("array-only schema keywords require type=array.")
    if kind == "string":
        for field in ("minLength", "maxLength"):
            if field in schema:
                normalized[field] = _bounded_integer(schema[field], field, 100_000)
        if normalized.get("minLength", 0) > normalized.get("maxLength", 100_000):
            raise ToolSchemaError("minLength cannot exceed maxLength.")
    elif "minLength" in schema or "maxLength" in schema:
        raise ToolSchemaError("string-only schema keywords require type=string.")
    if kind in {"integer", "number"}:
        for field in ("minimum", "maximum"):
            if field in schema:
                value = schema[field]
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                    raise ToolSchemaError(f"{field} must be a finite number.")
        if schema.get("minimum", -math.inf) > schema.get("maximum", math.inf):
            raise ToolSchemaError("minimum cannot exceed maximum.")
    elif "minimum" in schema or "maximum" in schema:
        raise ToolSchemaError("numeric bounds require type=integer or type=number.")
    if kind == "array":
        for field in ("minItems", "maxItems"):
            if field in schema:
                normalized[field] = _bounded_integer(schema[field], field, 100_000)
        if normalized.get("minItems", 0) > normalized.get("maxItems", 100_000):
            raise ToolSchemaError("minItems cannot exceed maxItems.")
    return normalized


def validate_tool_definitions(tools: Any) -> dict[str, dict[str, Any]]:
    """Validate the model-visible tool list and return name -> argument schema."""
    if not isinstance(tools, (list, tuple)) or len(tools) > 32:
        raise ToolSchemaError("tools must contain at most 32 definitions.")
    schemas: dict[str, dict[str, Any]] = {}
    for item in tools:
        if not isinstance(item, Mapping) or set(item) != {"type", "function"} or item.get("type") != "function":
            raise ToolSchemaError("each tool must use the function-tool envelope.")
        function = item["function"]
        if not isinstance(function, Mapping) or set(function) - {"name", "description", "parameters", "strict"}:
            raise ToolSchemaError("function definition contains unsupported fields.")
        name = function.get("name")
        description = function.get("description", "")
        if not isinstance(name, str) or not _NAME.fullmatch(name) or name in schemas:
            raise ToolSchemaError("tool names must be unique valid identifiers.")
        if not isinstance(description, str) or len(description) > 1024:
            raise ToolSchemaError("tool descriptions must be bounded text.")
        if "strict" in function and type(function["strict"]) is not bool:
            raise ToolSchemaError("tool strict flag must be a boolean.")
        schema = _validate_schema(function.get("parameters"))
        if schema["type"] != "object":
            raise ToolSchemaError("tool parameters must have a root object schema.")
        schemas[name] = schema
    return schemas


def _validate_value(value: Any, schema: Mapping[str, Any], path: str) -> None:
    kind = schema["type"]
    valid = {
        "object": lambda v: isinstance(v, dict),
        "array": lambda v: isinstance(v, list),
        "string": lambda v: isinstance(v, str),
        "integer": lambda v: type(v) is int,
        "number": lambda v: type(v) in (int, float) and math.isfinite(v),
        "boolean": lambda v: type(v) is bool,
        "null": lambda v: v is None,
    }[kind](value)
    if not valid:
        raise ToolSchemaError(f"{path} must be {kind}.")
    if "enum" in schema and value not in schema["enum"]:
        raise ToolSchemaError(f"{path} is not an allowed enum value.")
    if kind == "object":
        properties = schema.get("properties", {})
        missing = [key for key in schema.get("required", []) if key not in value]
        if missing:
            raise ToolSchemaError(f"{path} is missing required properties.")
        if schema.get("additionalProperties", True) is False and set(value) - set(properties):
            raise ToolSchemaError(f"{path} contains undeclared properties.")
        for key, child in value.items():
            if key in properties:
                _validate_value(child, properties[key], f"{path}.{key}")
    elif kind == "array":
        if len(value) < schema.get("minItems", 0) or len(value) > schema.get("maxItems", 100_000):
            raise ToolSchemaError(f"{path} has an invalid number of items.")
        if "items" in schema:
            for index, child in enumerate(value):
                _validate_value(child, schema["items"], f"{path}[{index}]")
    elif kind == "string":
        if len(value) < schema.get("minLength", 0) or len(value) > schema.get("maxLength", 100_000):
            raise ToolSchemaError(f"{path} has an invalid string length.")
    elif kind in {"integer", "number"}:
        if value < schema.get("minimum", -math.inf) or value > schema.get("maximum", math.inf):
            raise ToolSchemaError(f"{path} is outside its numeric bounds.")


def validate_tool_calls(calls: Any, schemas: Mapping[str, Mapping[str, Any]]) -> None:
    """Validate every proposal against exactly the tools advertised for this run."""
    for call in calls:
        schema = schemas.get(call.name)
        if schema is None:
            raise ToolSchemaError("model proposed a tool that was not advertised.")
        _validate_value(dict(call.arguments), schema, call.name)