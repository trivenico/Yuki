"""Typed, bounded contracts shared by Yuki's planning and tool-execution layers.

These objects describe requests and observations; they do not grant permission or
execute tools. Callers must still validate ToolCall against a registered ToolDefinition
and pass side-effecting calls through the existing approval/policy gates.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import json
import re
import hashlib
from typing import Any, Mapping
from types import MappingProxyType
from uuid import uuid4


_NAME_RE = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_MAX_ARGUMENTS = 16_000
_MAX_OUTPUT = 16_000
_MAX_EVIDENCE = 4_000
_MAX_EVENT_PAYLOAD = 24_000


class TaskStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    AWAITING_VERIFICATION = "awaiting_verification"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


def _require_text(value: str, label: str, *, maximum: int, allow_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be text.")
    normalized = value.strip()
    if (not normalized and not allow_empty) or len(value) > maximum:
        qualifier = "non-empty " if not allow_empty else ""
        raise ValueError(f"{label} must be {qualifier}text of at most {maximum} characters.")
    return normalized


def _freeze_json(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze_json(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze_json(item) for item in value)
    return value


def _thaw_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_json(item) for item in value]
    return value


def _validate_json_object(value: Mapping[str, Any], label: str, maximum: int) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object.")
    copied = dict(value)
    try:
        encoded = json.dumps(copied, ensure_ascii=True, allow_nan=False, sort_keys=True)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must contain only finite JSON values.") from exc
    if len(encoded) > maximum:
        raise ValueError(f"{label} exceeds {maximum} characters.")
    return copied


@dataclass(frozen=True)
class ToolDefinition:
    """A registered tool's public contract, not its authorization policy."""

    name: str
    description: str
    input_schema: Mapping[str, Any]
    risk: RiskLevel = RiskLevel.LOW
    side_effecting: bool = False
    capabilities: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not _NAME_RE.fullmatch(self.name):
            raise ValueError("Tool name must be a lowercase identifier.")
        _require_text(self.description, "Tool description", maximum=1000)
        schema = _validate_json_object(self.input_schema, "Tool input schema", 8_000)
        if schema.get("type") != "object":
            raise ValueError("Tool input schema must declare type='object'.")
        _validate_schema_definition(schema)
        if not isinstance(self.risk, RiskLevel):
            raise ValueError("Tool risk must be a RiskLevel.")
        if not isinstance(self.side_effecting, bool):
            raise ValueError("side_effecting must be a boolean.")
        if not isinstance(self.capabilities, tuple) or any(
            not isinstance(item, str) or not _NAME_RE.fullmatch(item) for item in self.capabilities
        ):
            raise ValueError("Capabilities must be a tuple of lowercase identifiers.")
        object.__setattr__(self, "input_schema", _freeze_json(schema))


@dataclass(frozen=True)
class ToolCall:
    """Untrusted model/user-requested invocation awaiting validation and authorization."""

    task_id: str
    tool_name: str
    arguments: Mapping[str, Any]
    call_id: str = field(default_factory=lambda: uuid4().hex)
    idempotency_key: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.task_id, str) or not _ID_RE.fullmatch(self.task_id):
            raise ValueError("Task ID is invalid.")
        if not isinstance(self.tool_name, str) or not _NAME_RE.fullmatch(self.tool_name):
            raise ValueError("Tool name is invalid.")
        if not isinstance(self.call_id, str) or not _ID_RE.fullmatch(self.call_id):
            raise ValueError("Tool call ID is invalid.")
        if self.idempotency_key is not None and (
            not isinstance(self.idempotency_key, str) or not _ID_RE.fullmatch(self.idempotency_key)
        ):
            raise ValueError("Idempotency key is invalid.")
        object.__setattr__(
            self, "arguments", _freeze_json(_validate_json_object(self.arguments, "Tool arguments", _MAX_ARGUMENTS))
        )

    @property
    def fingerprint(self) -> str:
        canonical = json.dumps(
            {"task_id": self.task_id, "tool_name": self.tool_name, "arguments": _thaw_json(self.arguments)},
            ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()



_SCHEMA_KEYS = {
    "type", "properties", "required", "additionalProperties", "items",
    "enum", "minLength", "maxLength", "minItems", "maxItems",
    "minimum", "maximum", "description", "title", "default",
}



def _validate_schema_definition(schema: Mapping[str, Any], path: str = "$") -> None:
    if not isinstance(schema, Mapping):
        raise ValueError(f"Invalid schema at {path}: expected an object.")
    unknown = set(schema) - _SCHEMA_KEYS
    if unknown:
        raise ValueError(f"Unsupported schema keyword(s) at {path}: {', '.join(sorted(unknown))}.")
    expected = schema.get("type")
    allowed_types = {"object", "array", "string", "boolean", "integer", "number", "null"}
    if expected not in allowed_types:
        raise ValueError(f"Unsupported or missing schema type at {path}.")
    for key in ("minLength", "maxLength", "minItems", "maxItems"):
        if key in schema and (
            isinstance(schema[key], bool) or not isinstance(schema[key], int) or schema[key] < 0
        ):
            raise ValueError(f"{key} must be a non-negative integer at {path}.")
    for key in ("minimum", "maximum"):
        if key in schema and (
            isinstance(schema[key], bool) or not isinstance(schema[key], (int, float))
        ):
            raise ValueError(f"{key} must be numeric at {path}.")
    for lower, upper in (("minLength", "maxLength"), ("minItems", "maxItems"), ("minimum", "maximum")):
        if lower in schema and upper in schema and schema[lower] > schema[upper]:
            raise ValueError(f"{lower} cannot exceed {upper} at {path}.")
    if "enum" in schema and not isinstance(schema["enum"], (list, tuple)):
        raise ValueError(f"enum must be a list at {path}.")
    if expected == "object":
        properties = schema.get("properties", {})
        required = schema.get("required", [])
        additional = schema.get("additionalProperties", True)
        if not isinstance(properties, Mapping) or not isinstance(required, (list, tuple)):
            raise ValueError(f"Invalid object schema at {path}.")
        if any(not isinstance(key, str) for key in properties):
            raise ValueError(f"Invalid property name in schema at {path}.")
        if any(not isinstance(key, str) for key in required) or len(set(required)) != len(required):
            raise ValueError(f"Invalid required list in schema at {path}.")
        if any(key not in properties for key in required):
            raise ValueError(f"Required fields must be declared in properties at {path}.")
        if not isinstance(additional, bool):
            raise ValueError(f"additionalProperties must be a boolean at {path}.")
        for key, child in properties.items():
            _validate_schema_definition(child, f"{path}.{key}")
    elif expected == "array":
        if "items" not in schema:
            raise ValueError(f"Array schema must declare items at {path}.")
        _validate_schema_definition(schema["items"], f"{path}[]")


def _schema_type_matches(value: Any, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, Mapping)
    if expected == "array":
        return isinstance(value, (list, tuple))
    if expected == "string":
        return isinstance(value, str)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "null":
        return value is None
    return False


def _validate_value_against_schema(value: Any, schema: Mapping[str, Any], path: str) -> None:
    if not isinstance(schema, Mapping):
        raise ValueError(f"Unsupported schema at {path}: expected an object.")
    unknown = set(schema) - _SCHEMA_KEYS
    if unknown:
        raise ValueError(f"Unsupported schema keyword(s) at {path}: {', '.join(sorted(unknown))}.")
    expected = schema.get("type")
    allowed_types = {"object", "array", "string", "boolean", "integer", "number", "null"}
    if expected not in allowed_types:
        raise ValueError(f"Unsupported or missing schema type at {path}.")
    if not _schema_type_matches(value, expected):
        raise ValueError(f"Tool argument {path} must be {expected}.")

    if "enum" in schema:
        choices = schema["enum"]
        if not isinstance(choices, (list, tuple)) or value not in choices:
            raise ValueError(f"Tool argument {path} is not an allowed value.")

    if expected == "object":
        properties = schema.get("properties", {})
        required = schema.get("required", [])
        additional = schema.get("additionalProperties", True)
        if not isinstance(properties, Mapping) or not isinstance(required, (list, tuple)):
            raise ValueError(f"Invalid object schema at {path}.")
        if any(not isinstance(key, str) for key in properties):
            raise ValueError(f"Invalid property name in schema at {path}.")
        if any(not isinstance(key, str) for key in required) or len(set(required)) != len(required):
            raise ValueError(f"Invalid required list in schema at {path}.")
        if any(key not in properties for key in required):
            raise ValueError(f"Required fields must be declared in properties at {path}.")
        if not isinstance(additional, bool):
            raise ValueError(f"additionalProperties must be a boolean at {path}.")
        missing = set(required) - set(value)
        if missing:
            raise ValueError(f"Missing required tool argument(s) at {path}: {', '.join(sorted(missing))}.")
        if not additional:
            extras = set(value) - set(properties)
            if extras:
                raise ValueError(f"Unexpected tool argument(s) at {path}: {', '.join(sorted(extras))}.")
        for key, child in value.items():
            if key in properties:
                _validate_value_against_schema(child, properties[key], f"{path}.{key}")

    elif expected == "array":
        if "items" not in schema:
            raise ValueError(f"Array schema must declare items at {path}.")
        if "minItems" in schema and len(value) < schema["minItems"]:
            raise ValueError(f"Tool argument {path} has too few items.")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            raise ValueError(f"Tool argument {path} has too many items.")
        for index, child in enumerate(value):
            _validate_value_against_schema(child, schema["items"], f"{path}[{index}]")

    elif expected == "string":
        if "minLength" in schema and len(value) < schema["minLength"]:
            raise ValueError(f"Tool argument {path} is too short.")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            raise ValueError(f"Tool argument {path} is too long.")

    elif expected in {"integer", "number"}:
        if "minimum" in schema and value < schema["minimum"]:
            raise ValueError(f"Tool argument {path} is below the minimum.")
        if "maximum" in schema and value > schema["maximum"]:
            raise ValueError(f"Tool argument {path} is above the maximum.")


def validate_tool_call(call: ToolCall, definition: ToolDefinition) -> None:
    """Fail closed unless the untrusted call matches a registered tool's schema.

    This deliberately supports a documented JSON Schema subset; unsupported keywords
    are rejected rather than silently ignored. This validates shape, not authorization.
    """
    if not isinstance(call, ToolCall) or not isinstance(definition, ToolDefinition):
        raise TypeError("A ToolCall and ToolDefinition are required.")
    if call.tool_name != definition.name:
        raise ValueError("Tool call name does not match the registered tool.")
    _validate_value_against_schema(call.arguments, definition.input_schema, "$")

@dataclass(frozen=True)
class ApprovalDecision:
    """A decision bound to one exact call fingerprint; it is not a reusable blanket grant."""

    call_id: str
    call_fingerprint: str
    approved: bool
    reason: str
    policy_version: str
    decided_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def matches(self, call: ToolCall) -> bool:
        """Return true only for the exact call that was reviewed."""
        return self.call_id == call.call_id and self.call_fingerprint == call.fingerprint

    def __post_init__(self) -> None:
        if not isinstance(self.call_id, str) or not _ID_RE.fullmatch(self.call_id):
            raise ValueError("Approval call ID is invalid.")
        if not isinstance(self.call_fingerprint, str) or not re.fullmatch(r"[a-f0-9]{64}", self.call_fingerprint):
            raise ValueError("Approval must bind to a valid tool-call fingerprint.")
        if not isinstance(self.approved, bool):
            raise ValueError("Approval decision must be boolean.")
        _require_text(self.reason, "Approval reason", maximum=1000)
        _require_text(self.policy_version, "Policy version", maximum=80)
        _require_text(self.decided_at, "Decision timestamp", maximum=80)


@dataclass(frozen=True)
class ToolResult:
    call_id: str
    ok: bool
    output: str = ""
    error_code: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.call_id, str) or not _ID_RE.fullmatch(self.call_id):
            raise ValueError("Tool result call ID is invalid.")
        if not isinstance(self.ok, bool):
            raise ValueError("Tool result ok must be boolean.")
        _require_text(self.output, "Tool output", maximum=_MAX_OUTPUT, allow_empty=True)
        _require_text(self.error_code, "Tool error code", maximum=80, allow_empty=True)
        if self.ok and self.error_code:
            raise ValueError("Successful tool results cannot contain an error code.")
        if not self.ok and not self.error_code:
            raise ValueError("Failed tool results require an error code.")


@dataclass(frozen=True)
class VerificationResult:
    verified: bool
    summary: str
    evidence: str
    verifier: str

    def __post_init__(self) -> None:
        if not isinstance(self.verified, bool):
            raise ValueError("Verification status must be boolean.")
        _require_text(self.summary, "Verification summary", maximum=1000)
        _require_text(self.evidence, "Verification evidence", maximum=_MAX_EVIDENCE)
        _require_text(self.verifier, "Verifier", maximum=120)
        if self.verified and not self.evidence.strip():
            raise ValueError("Verified completion requires evidence.")


@dataclass(frozen=True)
class TaskEvent:
    """Append-friendly, bounded event envelope for task timelines and trace replay."""

    task_id: str
    event_type: str
    payload: Mapping[str, Any]
    event_id: str = field(default_factory=lambda: uuid4().hex)
    occurred_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    schema_version: int = 1

    def __post_init__(self) -> None:
        if not isinstance(self.task_id, str) or not _ID_RE.fullmatch(self.task_id):
            raise ValueError("Task event ID is invalid.")
        if not isinstance(self.event_id, str) or not _ID_RE.fullmatch(self.event_id):
            raise ValueError("Event ID is invalid.")
        if not isinstance(self.event_type, str) or not _NAME_RE.fullmatch(self.event_type):
            raise ValueError("Event type is invalid.")
        if isinstance(self.schema_version, bool) or self.schema_version != 1:
            raise ValueError("Unsupported task event schema version.")
        _require_text(self.occurred_at, "Event timestamp", maximum=80)
        object.__setattr__(
            self,
            "payload",
            _freeze_json(_validate_json_object(self.payload, "Task event payload", _MAX_EVENT_PAYLOAD)),
        )
