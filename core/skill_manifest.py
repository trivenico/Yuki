"""Versioned declarative skill contracts; metadata is not an execution sandbox."""
from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any


_SKILL_ID = re.compile(r"^[a-z][a-z0-9_.-]{1,79}$")
_VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+$")
_ALLOWED_CAPABILITIES = frozenset({
    "workspace.read", "workspace.write", "network.http", "process.spawn",
    "memory.read", "memory.write", "messaging.send", "clipboard.read",
})
_SCHEMA_TYPES = frozenset({"object", "array", "string", "integer", "number", "boolean", "null"})


def _validate_schema(schema: Any, *, depth: int = 0) -> dict:
    if depth > 8 or not isinstance(schema, dict):
        raise ValueError("Schema must be an object with nesting depth at most 8.")
    allowed = {"type", "properties", "required", "additionalProperties", "items", "enum", "maxLength", "maxItems"}
    if set(schema) - allowed:
        raise ValueError("Schema contains unsupported keywords.")
    kind = schema.get("type")
    if kind not in _SCHEMA_TYPES:
        raise ValueError("Schema type is unsupported.")
    if "enum" in schema:
        enum = schema["enum"]
        if not isinstance(enum, list) or not 1 <= len(enum) <= 100:
            raise ValueError("enum must be a non-empty list of at most 100 values.")
        try:
            encoded = [json.dumps(item, allow_nan=False, sort_keys=True) for item in enum]
        except (TypeError, ValueError) as exc:
            raise ValueError("enum values must be JSON-compatible.") from exc
        if len(set(encoded)) != len(encoded):
            raise ValueError("enum values must be unique.")
    if "maxLength" in schema and (type(schema["maxLength"]) is not int or not 0 <= schema["maxLength"] <= 65536):
        raise ValueError("maxLength must be between 0 and 65536.")
    if "maxItems" in schema and (type(schema["maxItems"]) is not int or not 0 <= schema["maxItems"] <= 10000):
        raise ValueError("maxItems must be between 0 and 10000.")
    if kind == "object":
        properties = schema.get("properties", {})
        required = schema.get("required", [])
        if not isinstance(properties, dict) or len(properties) > 100:
            raise ValueError("Object properties must be a mapping of at most 100 fields.")
        if not all(isinstance(key, str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,79}", key) for key in properties):
            raise ValueError("Object property names are invalid.")
        if not isinstance(required, list) or len(required) > len(properties) or not all(key in properties for key in required):
            raise ValueError("required must reference declared properties.")
        for child in properties.values():
            _validate_schema(child, depth=depth + 1)
        if schema.get("additionalProperties", False) is not False:
            raise ValueError("additionalProperties must be false.")
    elif kind == "array":
        if "items" not in schema:
            raise ValueError("Array schema requires items.")
        _validate_schema(schema["items"], depth=depth + 1)
    return schema


def _matches(value: Any, schema: dict) -> bool:
    if "enum" in schema and value not in schema["enum"]:
        return False
    kind = schema["type"]
    if kind == "object":
        if not isinstance(value, dict):
            return False
        props = schema.get("properties", {})
        if any(key not in props for key in value):
            return False
        if any(key not in value for key in schema.get("required", [])):
            return False
        return all(_matches(item, props[key]) for key, item in value.items())
    if kind == "array":
        return (isinstance(value, list) and len(value) <= schema.get("maxItems", 10000)
                and all(_matches(item, schema["items"]) for item in value))
    if kind == "string":
        return isinstance(value, str) and len(value) <= schema.get("maxLength", 65536)
    if kind == "integer":
        return type(value) is int
    if kind == "number":
        return type(value) in (int, float) and not isinstance(value, bool)
    if kind == "boolean":
        return type(value) is bool
    return value is None


@dataclass(frozen=True)
class SkillManifest:
    skill_id: str
    version: str
    entrypoint: str
    input_schema: dict
    output_schema: dict
    capabilities: frozenset[str]
    risk: str = "medium"
    timeout_seconds: int = 15
    max_output_bytes: int = 65536
    parallel_safe: bool = False
    resource_group: str = "default"

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "SkillManifest":
        if not isinstance(value, dict):
            raise ValueError("Skill manifest must be an object.")
        allowed = {"skill_id", "version", "entrypoint", "input_schema", "output_schema",
                   "capabilities", "risk", "timeout_seconds", "max_output_bytes",
                   "parallel_safe", "resource_group"}
        if set(value) - allowed:
            raise ValueError("Skill manifest contains unknown fields.")
        skill_id = value.get("skill_id")
        version = value.get("version")
        entrypoint = value.get("entrypoint")
        if not isinstance(skill_id, str) or not _SKILL_ID.fullmatch(skill_id):
            raise ValueError("skill_id must be a stable lowercase identifier.")
        if not isinstance(version, str) or not _VERSION.fullmatch(version):
            raise ValueError("version must use MAJOR.MINOR.PATCH.")
        if not isinstance(entrypoint, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.]{0,119}", entrypoint):
            raise ValueError("entrypoint must be a qualified identifier, not a path or command.")
        input_schema = _validate_schema(value.get("input_schema"))
        output_schema = _validate_schema(value.get("output_schema"))
        caps = value.get("capabilities", [])
        if not isinstance(caps, list) or len(caps) > len(_ALLOWED_CAPABILITIES):
            raise ValueError("capabilities must be a bounded list.")
        if any(not isinstance(cap, str) or cap not in _ALLOWED_CAPABILITIES for cap in caps):
            raise ValueError("Manifest requests an unknown or unsupported capability.")
        risk = value.get("risk", "medium")
        if risk not in {"low", "medium", "high"}:
            raise ValueError("risk must be low, medium, or high.")
        timeout = value.get("timeout_seconds", 15)
        if type(timeout) is not int or not 1 <= timeout <= 120:
            raise ValueError("timeout_seconds must be between 1 and 120.")
        output_bytes = value.get("max_output_bytes", 65536)
        if type(output_bytes) is not int or not 1 <= output_bytes <= 1_048_576:
            raise ValueError("max_output_bytes must be between 1 and 1048576.")
        parallel_safe = value.get("parallel_safe", False)
        if type(parallel_safe) is not bool:
            raise ValueError("parallel_safe must be a boolean.")
        resource_group = value.get("resource_group", "default")
        if not isinstance(resource_group, str) or not re.fullmatch(r"[a-z][a-z0-9_.-]{0,63}", resource_group):
            raise ValueError("resource_group must be a bounded identifier.")
        if parallel_safe and any(cap in caps for cap in {"workspace.write", "messaging.send", "memory.write", "process.spawn"}):
            raise ValueError("Side-effecting capabilities cannot be declared parallel-safe.")
        return cls(skill_id, version, entrypoint, input_schema, output_schema, frozenset(caps),
                   risk, timeout, output_bytes, parallel_safe, resource_group)

    def validate_input(self, value: Any) -> None:
        if not _matches(value, self.input_schema):
            raise ValueError("Skill input does not match its declared schema.")

    def validate_output(self, value: Any) -> None:
        try:
            encoded = json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise ValueError("Skill output is not JSON-compatible.") from exc
        if len(encoded) > self.max_output_bytes:
            raise ValueError("Skill output exceeds the manifest byte limit.")
        if not _matches(value, self.output_schema):
            raise ValueError("Skill output does not match its declared schema.")
