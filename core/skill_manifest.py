"""Validated, immutable manifest metadata for isolated skill subprocesses."""
from __future__ import annotations
from dataclasses import dataclass
import json
import re
from typing import Any

_SKILL_ID = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,119}$")
_CAPABILITY = re.compile(r"^[a-z][a-z0-9_.-]{0,79}$")

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
    def from_dict(cls, value: dict[str, Any]) -> "SkillManifest":
        if not isinstance(value, dict):
            raise ValueError("Skill manifest must be an object.")
        sid, version, entrypoint = value.get("skill_id"), value.get("version"), value.get("entrypoint")
        if not isinstance(sid, str) or not _SKILL_ID.fullmatch(sid):
            raise ValueError("Invalid skill_id.")
        if not isinstance(version, str) or not re.fullmatch(r"\d+\.\d+\.\d+", version):
            raise ValueError("version must use semantic version X.Y.Z.")
        if not isinstance(entrypoint, str) or not re.fullmatch(r"[A-Za-z_]\w*(\.[A-Za-z_]\w*)+", entrypoint):
            raise ValueError("Invalid entrypoint.")
        ins, outs = value.get("input_schema"), value.get("output_schema")
        if not isinstance(ins, dict) or not isinstance(outs, dict):
            raise ValueError("input_schema and output_schema must be objects.")
        if ins.get("type") != "object" or outs.get("type") != "object":
            raise ValueError("Skill input and output schemas must be object schemas.")
        caps = value.get("capabilities", [])
        if not isinstance(caps, list) or any(not isinstance(c, str) or not _CAPABILITY.fullmatch(c) for c in caps):
            raise ValueError("Invalid capabilities.")
        if len(set(caps)) != len(caps):
            raise ValueError("Duplicate capabilities are not allowed.")
        risk = value.get("risk", "high")
        if risk not in {"low", "medium", "high", "critical"}:
            raise ValueError("Invalid risk level.")
        timeout = value.get("timeout_seconds", 5)
        output_limit = value.get("max_output_bytes", 65536)
        if type(timeout) is not int or not 1 <= timeout <= 120:
            raise ValueError("timeout_seconds must be between 1 and 120.")
        if type(output_limit) is not int or not 1 <= output_limit <= 1_000_000:
            raise ValueError("max_output_bytes must be between 1 and 1000000.")
        # Ensure manifests are JSON-safe and reasonably small.
        try:
            if len(json.dumps(value, allow_nan=False).encode()) > 64 * 1024:
                raise ValueError("Manifest exceeds 64 KiB.")
        except (TypeError, ValueError) as exc:
            raise ValueError("Manifest must contain JSON-safe values.") from exc
        return cls(sid, version, entrypoint, ins, outs, tuple(caps), risk, timeout, output_limit)

    @staticmethod
    def validate_object(schema: dict[str, Any], data: Any) -> bool:
        if not isinstance(data, dict) or schema.get("type") != "object":
            return False
        required = schema.get("required", [])
        props = schema.get("properties", {})
        if not isinstance(required, list) or any(k not in data for k in required):
            return False
        if schema.get("additionalProperties", True) is False and any(k not in props for k in data):
            return False
        for key, val in data.items():
            rule = props.get(key, {})
            typ = rule.get("type")
            if typ == "string":
                if not isinstance(val, str) or len(val) > rule.get("maxLength", 1_000_000):
                    return False
                if len(val) < rule.get("minLength", 0):
                    return False
            elif typ == "boolean" and type(val) is not bool:
                return False
            elif typ == "integer" and type(val) is not int:
                return False
            elif typ == "number" and (isinstance(val, bool) or not isinstance(val, (int, float))):
                return False
            elif typ == "object" and not isinstance(val, dict):
                return False
            elif typ == "array" and not isinstance(val, list):
                return False
        return True
