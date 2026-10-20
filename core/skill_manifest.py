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
        skill_id, version, entrypoint = value.get("skill_id"), value.get("version"), value.get("entrypoint")
        if not isinstance(skill_id, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,120}", skill_id):
            raise ValueError("invalid skill_id")
        if not isinstance(version, str) or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version):
            raise ValueError("version must be SemVer")
        if not isinstance(entrypoint, str) or not re.fullmatch(r"[A-Za-z_]\w*(\.[A-Za-z_]\w*)*", entrypoint):
            raise ValueError("invalid entrypoint")
        schemas = (value.get("input_schema"), value.get("output_schema"))
        for schema in schemas:
            if not isinstance(schema, dict) or schema.get("type") != "object":
                raise ValueError("schemas must be object schemas")
            if schema.get("additionalProperties", False) is not False:
                raise ValueError("additionalProperties must be false")
            if not isinstance(schema.get("properties", {}), dict):
                raise ValueError("properties must be an object")
        capabilities = value.get("capabilities", [])
        if not isinstance(capabilities, list) or any(not isinstance(c, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", c) for c in capabilities):
            raise ValueError("invalid capabilities")
        risk = value.get("risk")
        if risk not in {"low", "medium", "high"}:
            raise ValueError("invalid risk")
        timeout, limit = value.get("timeout_seconds"), value.get("max_output_bytes")
        if type(timeout) is not int or not 1 <= timeout <= 60:
            raise ValueError("timeout_seconds must be between 1 and 60")
        if type(limit) is not int or not 1 <= limit <= 1048576:
            raise ValueError("max_output_bytes must be between 1 and 1048576")
        return cls(skill_id, version, entrypoint, schemas[0], schemas[1], tuple(capabilities), risk, timeout, limit)

    @staticmethod
    def validate_object(value, schema):
        if not isinstance(value, dict):
            raise ValueError("value must be an object")
        properties, required = schema.get("properties", {}), schema.get("required", [])
        if not isinstance(required, list) or any(key not in value for key in required):
            raise ValueError("required field missing")
        if schema.get("additionalProperties", False) is False and any(key not in properties for key in value):
            raise ValueError("unexpected field")
        for key, item in value.items():
            rule = properties.get(key, {})
            kind = rule.get("type")
            checks = {"string": lambda x: isinstance(x, str), "boolean": lambda x: type(x) is bool,
                      "integer": lambda x: type(x) is int, "number": lambda x: type(x) in (int, float),
                      "object": lambda x: isinstance(x, dict), "array": lambda x: isinstance(x, list)}
            if kind in checks and not checks[kind](item):
                raise ValueError("invalid field type")
            if isinstance(item, str) and isinstance(rule.get("maxLength"), int) and len(item) > rule["maxLength"]:
                raise ValueError("field too long")
        return value
