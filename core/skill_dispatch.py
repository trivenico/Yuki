"""Manifest-validated skill dispatch with mandatory one-time approval."""
import json

from core.action_approvals import action_fingerprint
from core.permissions import PermissionGate


MAX_ARGUMENT_BYTES = 20_000
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
_ALLOWED_RISKS = {"low", "medium", "high"}


def _validate_schema(value, schema, path="$"):
    """Validate the intentionally small JSON Schema subset used by skill manifests."""
    if not isinstance(schema, dict):
        raise ValueError(f"Invalid schema at {path}.")
    expected = schema.get("type")
    type_checks = {
        "object": lambda v: isinstance(v, dict),
        "array": lambda v: isinstance(v, list),
        "string": lambda v: isinstance(v, str),
        "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
        "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
        "boolean": lambda v: isinstance(v, bool),
        "null": lambda v: v is None,
    }
    if expected in type_checks and not type_checks[expected](value):
        raise ValueError(f"Invalid value type at {path}.")
    if expected == "object":
        properties = schema.get("properties", {})
        required = schema.get("required", [])
        if not isinstance(required, list) or any(not isinstance(k, str) for k in required):
            raise ValueError(f"Invalid required list at {path}.")
        for key in required:
            if key not in value:
                raise ValueError(f"Missing required field at {path}.{key}.")
        if schema.get("additionalProperties", True) is False:
            extras = set(value) - set(properties)
            if extras:
                raise ValueError(f"Unexpected field at {path}.")
        for key, item in value.items():
            if key in properties:
                _validate_schema(item, properties[key], f"{path}.{key}")
    elif expected == "array":
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            raise ValueError(f"Array too large at {path}.")
        if "items" in schema:
            for index, item in enumerate(value):
                _validate_schema(item, schema["items"], f"{path}[{index}]")
    elif expected == "string":
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            raise ValueError(f"String too long at {path}.")
        if "enum" in schema and value not in schema["enum"]:
            raise ValueError(f"Invalid value at {path}.")
    elif expected in {"integer", "number"}:
        if "minimum" in schema and value < schema["minimum"]:
            raise ValueError(f"Number too small at {path}.")
        if "maximum" in schema and value > schema["maximum"]:
            raise ValueError(f"Number too large at {path}.")


class SkillDispatcher:
    def __init__(self, capability_grants=None, approval_store=None, permission_gate=None):
        self.capability_grants = set(capability_grants or [])
        self.approval_store = approval_store
        self.permission_gate = permission_gate
        self.registered = {}

    def register_subprocess(self, manifest, cmd, executor=None):
        if not isinstance(manifest.skill_id, str) or not manifest.skill_id.strip():
            raise ValueError("Invalid skill id.")
        if not isinstance(cmd, (list, tuple)) or not cmd or any(not isinstance(part, str) or not part for part in cmd):
            raise ValueError("Subprocess command must be a non-empty argument list.")
        if not isinstance(manifest.capabilities, list):
            raise ValueError("Invalid capability declaration.")
        if manifest.risk not in _ALLOWED_RISKS:
            raise ValueError("Invalid risk level.")
        if not isinstance(manifest.timeout_seconds, int) or isinstance(manifest.timeout_seconds, bool) or not 1 <= manifest.timeout_seconds <= 30:
            raise ValueError("Skill timeout must be between 1 and 30 seconds.")
        if not isinstance(manifest.max_output_bytes, int) or isinstance(manifest.max_output_bytes, bool) or not 1 <= manifest.max_output_bytes <= MAX_RESPONSE_BYTES:
            raise ValueError("Skill output limit is invalid.")
        if not isinstance(manifest.input_schema, dict) or not isinstance(manifest.output_schema, dict):
            raise ValueError("Skill schemas must be objects.")
        self.registered[manifest.skill_id] = {
            "manifest": manifest,
            "cmd": list(cmd),
            "executor": executor,
        }

    def dispatch(self, skill_id, arguments):
        if not isinstance(skill_id, str) or skill_id not in self.registered:
            raise ValueError("Unknown skill.")
        if not isinstance(arguments, dict):
            raise ValueError("Skill arguments must be an object.")
        # Canonicalize through strict JSON to detach the approved payload from caller-owned
        # mutable objects and reject NaN, infinities, custom objects, and invalid nesting.
        try:
            input_json = json.dumps(arguments, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        except (TypeError, ValueError, RecursionError) as exc:
            raise ValueError("Skill arguments must be finite JSON-compatible values.") from exc
        if len(input_json.encode("utf-8")) > MAX_ARGUMENT_BYTES:
            raise ValueError("Skill arguments exceed the size limit.")
        arguments = json.loads(input_json)
        action = {"skill_id": skill_id, "arguments": arguments}
        entry = self.registered[skill_id]
        manifest = entry["manifest"]
        for cap in manifest.capabilities:
            if cap not in self.capability_grants:
                raise PermissionError("Missing capability grant.")
        _validate_schema(arguments, manifest.input_schema)

        # Every execution requires the existing fail-closed human permission gate.
        if self.permission_gate is None or not isinstance(self.permission_gate, PermissionGate):
            raise PermissionError("Permission gate is unavailable.")
        if self.permission_gate.request(
            action=skill_id,
            reason="Approve this exact validated skill action: " + action_fingerprint(action),
            risk=manifest.risk,
        ) is not True:
            raise PermissionError("Human approval denied.")
        if self.approval_store is None or not self.approval_store.consume(action_fingerprint(action)):
            raise PermissionError("A fresh approval for this exact action is required.")

        executor = entry["executor"]
        if executor is None or not callable(getattr(executor, "run", None)):
            raise RuntimeError("A bounded subprocess executor is required.")
        stdout, stderr, code = executor.run(
            entry["cmd"], input_data=input_json,
            timeout_seconds=manifest.timeout_seconds,
            max_output_bytes=manifest.max_output_bytes,
        )
        if code != 0:
            raise RuntimeError("Skill process failed.")
        if len(stdout.encode("utf-8")) > manifest.max_output_bytes or len(stderr.encode("utf-8")) > manifest.max_output_bytes:
            raise RuntimeError("Skill output exceeded the configured limit.")
        if len(stdout.encode("utf-8")) > MAX_RESPONSE_BYTES:
            raise RuntimeError("Skill response exceeded the global size limit.")
        try:
            result = json.loads(stdout)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ValueError("Skill returned invalid JSON.") from exc
        _validate_schema(result, manifest.output_schema)
        return result
