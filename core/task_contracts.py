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
        if not isinstance(self.risk, RiskLevel):
            raise ValueError("Tool risk must be a RiskLevel.")
        if not isinstance(self.side_effecting, bool):
            raise ValueError("side_effecting must be a boolean.")
        if not isinstance(self.capabilities, tuple) or any(
            not isinstance(item, str) or not _NAME_RE.fullmatch(item) for item in self.capabilities
        ):
            raise ValueError("Capabilities must be a tuple of lowercase identifiers.")
        object.__setattr__(self, "input_schema", schema)


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
            self, "arguments", _validate_json_object(self.arguments, "Tool arguments", _MAX_ARGUMENTS)
        )

    @property
    def fingerprint(self) -> str:
        canonical = json.dumps(
            {"task_id": self.task_id, "tool_name": self.tool_name, "arguments": self.arguments},
            ensure_ascii=True, allow_nan=False, sort_keys=True, separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


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
            self, "payload", _validate_json_object(self.payload, "Task event payload", _MAX_EVENT_PAYLOAD)
        )
