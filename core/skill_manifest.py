"""Validated manifest metadata for bounded isolated skills."""

from dataclasses import dataclass
import re
from typing import Any


@dataclass(frozen=True)
class SkillManifest:
    skill_id: str
    version: str
    entrypoint: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    capabilities: tuple[str, ...]
    risk: str
    timeout_seconds: int
    max_output_bytes: int

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, dict):
            raise ValueError("manifest must be an object")
        skill_id, version, entrypoint = (
            value.get("skill_id"),
            value.get("version"),
            value.get("entrypoint"),
        )
        if not isinstance(skill_id, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,120}", skill_id):
            raise ValueError("invalid skill_id")
        if not isinstance(version, str) or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version):
            raise ValueError("version must be SemVer")
        if not isinstance(entrypoint, str) or not re.fullmatch(
            r"[A-Za-z_]\w*(\.[A-Za-z_]\w*)*", entrypoint
        ):
            raise ValueError("invalid entrypoint")
        schemas = (value.get("input_schema"), value.get("output_schema"))
        for schema in schemas:
            SkillManifest._validate_schema(schema, root=True)
        capabilities = value.get("capabilities", [])
        if not isinstance(capabilities, list) or any(
            not isinstance(c, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", c)
            for c in capabilities
        ):
            raise ValueError("invalid capabilities")
        risk = value.get("risk")
        if risk not in {"low", "medium", "high"}:
            raise ValueError("invalid risk")
        timeout, limit = value.get("timeout_seconds"), value.get("max_output_bytes")
        if type(timeout) is not int or not 1 <= timeout <= 60:
            raise ValueError("timeout_seconds must be between 1 and 60")
        if type(limit) is not int or not 1 <= limit <= 1048576:
            raise ValueError("max_output_bytes must be between 1 and 1048576")
        return cls(
            skill_id,
            version,
            entrypoint,
            schemas[0],
            schemas[1],
            tuple(capabilities),
            risk,
            timeout,
            limit,
        )

    @staticmethod
    def _validate_schema(schema, *, root=False, depth=0):
        """Reject unsupported or malformed schema rules at registration time."""
        if depth > 16 or not isinstance(schema, dict):
            raise ValueError("invalid schema")
        allowed = {
            "type", "properties", "required", "additionalProperties", "maxLength",
            "minLength", "minimum", "maximum", "enum", "items",
        }
        if set(schema) - allowed:
            raise ValueError("unsupported schema keyword")
        kind = schema.get("type")
        supported = {"object", "string", "boolean", "integer", "number", "array"}
        if kind not in supported or (root and kind != "object"):
            raise ValueError("unsupported schema type")
        if kind == "object":
            if schema.get("additionalProperties", False) is not False:
                raise ValueError("additionalProperties must be false")
            properties = schema.get("properties", {})
            required = schema.get("required", [])
            if not isinstance(properties, dict) or len(properties) > 128:
                raise ValueError("properties must be a bounded object")
            if not isinstance(required, list) or any(
                not isinstance(key, str) or key not in properties for key in required
            ) or len(set(required)) != len(required):
                raise ValueError("required must name unique declared properties")
            for key, rule in properties.items():
                if not isinstance(key, str) or not key or len(key) > 128:
                    raise ValueError("invalid property name")
                SkillManifest._validate_schema(rule, depth=depth + 1)
        elif "properties" in schema or "required" in schema or "additionalProperties" in schema:
            raise ValueError("object keywords require object type")
        if kind == "array":
            if "items" not in schema:
                raise ValueError("array schemas require items")
            SkillManifest._validate_schema(schema["items"], depth=depth + 1)
        elif "items" in schema:
            raise ValueError("items requires array type")
        for key in ("minLength", "maxLength"):
            if key in schema and (type(schema[key]) is not int or not 0 <= schema[key] <= 65536):
                raise ValueError("invalid string length bound")
        if "minLength" in schema and "maxLength" in schema and schema["minLength"] > schema["maxLength"]:
            raise ValueError("minLength exceeds maxLength")
        for key in ("minimum", "maximum"):
            if key in schema and (
                type(schema[key]) not in (int, float)
                or not __import__("math").isfinite(schema[key])
            ):
                raise ValueError("invalid numeric bound")
        if "minimum" in schema and "maximum" in schema and schema["minimum"] > schema["maximum"]:
            raise ValueError("minimum exceeds maximum")
        if "enum" in schema:
            choices = schema["enum"]
            if not isinstance(choices, list) or not choices or len(choices) > 256:
                raise ValueError("enum must be a bounded non-empty list")
            if any(not SkillManifest._matches_type(choice, kind) for choice in choices):
                raise ValueError("enum values must match schema type")

    @staticmethod
    def _matches_type(value, kind):
        import math

        if kind == "string":
            return isinstance(value, str)
        if kind == "boolean":
            return type(value) is bool
        if kind == "integer":
            return type(value) is int
        if kind == "number":
            return type(value) in (int, float) and math.isfinite(value)
        if kind == "object":
            return isinstance(value, dict)
        if kind == "array":
            return isinstance(value, list)
        return False

    @staticmethod
    def validate_object(value, schema):
        """Validate supported JSON-schema constraints recursively and fail closed."""
        SkillManifest._validate_schema(schema, root=True)
        if not isinstance(value, dict):
            raise ValueError("value must be an object")
        SkillManifest._validate_value(value, schema, depth=0)
        return value

    @staticmethod
    def _validate_value(value, schema, *, depth):
        if depth > 16:
            raise ValueError("value nesting exceeds limit")
        kind = schema["type"]
        if not SkillManifest._matches_type(value, kind):
            raise ValueError("invalid value type")
        if kind == "object":
            properties = schema.get("properties", {})
            required = schema.get("required", [])
            if any(key not in value for key in required):
                raise ValueError("required field missing")
            if schema.get("additionalProperties", False) is False and any(
                key not in properties for key in value
            ):
                raise ValueError("unexpected field")
            for key, item in value.items():
                SkillManifest._validate_value(item, properties[key], depth=depth + 1)
        elif kind == "array":
            if len(value) > 4096:
                raise ValueError("array exceeds item limit")
            for item in value:
                SkillManifest._validate_value(item, schema["items"], depth=depth + 1)
        elif kind == "string":
            if "minLength" in schema and len(value) < schema["minLength"]:
                raise ValueError("string too short")
            if "maxLength" in schema and len(value) > schema["maxLength"]:
                raise ValueError("string too long")
        elif kind in {"integer", "number"}:
            if "minimum" in schema and value < schema["minimum"]:
                raise ValueError("number below minimum")
            if "maximum" in schema and value > schema["maximum"]:
                raise ValueError("number above maximum")
        if "enum" in schema and value not in schema["enum"]:
            raise ValueError("value is not in enum")
