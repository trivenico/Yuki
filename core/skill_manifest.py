"""Validated declarative contract for isolated skill execution."""
from dataclasses import dataclass
import re

_ID = re.compile(r"^[a-z][a-z0-9_.-]{1,119}$")

@dataclass(frozen=True)
class SkillManifest:
    skill_id: str
    version: str
    entrypoint: str
    input_schema: dict
    output_schema: dict
    capabilities: tuple[str, ...]
    risk: str
    timeout_seconds: int
    max_output_bytes: int

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, dict): raise ValueError("manifest must be an object")
        skill_id, version, entrypoint = value.get("skill_id"), value.get("version"), value.get("entrypoint")
        if not isinstance(skill_id, str) or not _ID.fullmatch(skill_id): raise ValueError("invalid skill_id")
        if not isinstance(version, str) or not re.fullmatch(r"\d+\.\d+\.\d+", version): raise ValueError("version must be semver")
        if not isinstance(entrypoint, str) or not re.fullmatch(r"[A-Za-z_]\w*(\.[A-Za-z_]\w*)+", entrypoint): raise ValueError("invalid entrypoint")
        ins, outs = value.get("input_schema"), value.get("output_schema")
        for schema in (ins, outs):
            if not isinstance(schema, dict) or schema.get("type") != "object" or not isinstance(schema.get("properties", {}), dict):
                raise ValueError("input/output schema must be an object schema")
            if not isinstance(schema.get("required", []), list): raise ValueError("schema required must be a list")
            if schema.get("additionalProperties", True) is not False: raise ValueError("additionalProperties must be false")
        caps = value.get("capabilities", [])
        if not isinstance(caps, list) or any(not isinstance(c, str) or not re.fullmatch(r"[a-z][a-z0-9_.-]{1,79}", c) for c in caps): raise ValueError("invalid capabilities")
        risk = value.get("risk")
        if risk not in {"low", "medium", "high", "critical"}: raise ValueError("invalid risk")
        timeout, output = value.get("timeout_seconds", 5), value.get("max_output_bytes", 4096)
        if type(timeout) is not int or not 1 <= timeout <= 120: raise ValueError("timeout_seconds must be 1..120")
        if type(output) is not int or not 128 <= output <= 1_048_576: raise ValueError("max_output_bytes must be 128..1048576")
        return cls(skill_id, version, entrypoint, ins, outs, tuple(caps), risk, timeout, output)

def validate_object(value, schema):
    if not isinstance(value, dict): raise ValueError("arguments/result must be an object")
    props, required = schema.get("properties", {}), schema.get("required", [])
    if any(key not in value for key in required): raise ValueError("required field missing")
    if schema.get("additionalProperties") is False and any(key not in props for key in value): raise ValueError("unknown field")
    for key, item in value.items():
        spec = props.get(key)
        if spec is None: continue
        typ = spec.get("type")
        if typ == "string":
            if not isinstance(item, str): raise ValueError(f"{key} must be a string")
            if isinstance(spec.get("maxLength"), int) and len(item) > spec["maxLength"]: raise ValueError(f"{key} is too long")
        elif typ == "boolean" and type(item) is not bool: raise ValueError(f"{key} must be boolean")
        elif typ == "integer" and type(item) is not int: raise ValueError(f"{key} must be integer")
        elif typ == "number" and (type(item) not in (int, float) or isinstance(item, bool)): raise ValueError(f"{key} must be numeric")
        elif typ == "object" and not isinstance(item, dict): raise ValueError(f"{key} must be an object")
        elif typ == "array" and not isinstance(item, list): raise ValueError(f"{key} must be an array")
    return value
