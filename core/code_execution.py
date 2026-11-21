"""Provider-neutral contract for bounded generated-code execution.

This module defines data contracts only. It does not authenticate grants, run code,
or provide a sandbox. A production adapter must verify the signed grant with Yuki's
control plane and route every tool call through the supplied host-side dispatcher.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import re
from typing import Callable, Mapping, Protocol, runtime_checkable

_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,160}$")
_TOOL = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,127}$")
_MAX_SOURCE_BYTES = 65_536
_MAX_TOOLS = 32
_MAX_RESULT_BYTES = 1_048_576


class CodeExecutionContractError(ValueError):
    """An execution request, grant, or result violates the shared contract."""


@dataclass(frozen=True, slots=True)
class CodeExecutionRequest:
    """Immutable exact workload and resource budget presented to an executor."""

    execution_id: str
    actor_id: str
    session_id: str
    task_id: str
    source: str
    tool_names: tuple[str, ...] = ()
    timeout_seconds: int = 30
    max_tool_calls: int = 20
    max_result_bytes: int = 262_144
    network_access: bool = False

    def validate(self) -> "CodeExecutionRequest":
        for name in ("execution_id", "actor_id", "session_id", "task_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not _ID.fullmatch(value):
                raise CodeExecutionContractError(f"{name} must be a bounded identifier")
        if not isinstance(self.source, str) or not self.source.strip():
            raise CodeExecutionContractError("source must be non-empty text")
        try:
            source_size = len(self.source.encode("utf-8", errors="strict"))
        except UnicodeError as exc:
            raise CodeExecutionContractError("source contains invalid Unicode") from exc
        if source_size > _MAX_SOURCE_BYTES:
            raise CodeExecutionContractError("source exceeds the 64 KiB limit")
        if not isinstance(self.tool_names, tuple) or len(self.tool_names) > _MAX_TOOLS:
            raise CodeExecutionContractError("tool_names must be a tuple of at most 32 tools")
        if any(not isinstance(name, str) or not _TOOL.fullmatch(name) for name in self.tool_names):
            raise CodeExecutionContractError("tool_names must be registered-tool identifiers")
        if len(set(self.tool_names)) != len(self.tool_names):
            raise CodeExecutionContractError("tool_names must be unique")
        for name, value, low, high in (
            ("timeout_seconds", self.timeout_seconds, 1, 300),
            ("max_tool_calls", self.max_tool_calls, 0, 100),
            ("max_result_bytes", self.max_result_bytes, 1, _MAX_RESULT_BYTES),
        ):
            if type(value) is not int or not low <= value <= high:
                raise CodeExecutionContractError(f"{name} is outside the permitted range")
        if type(self.network_access) is not bool:
            raise CodeExecutionContractError("network_access must be a boolean")
        if self.network_access:
            raise CodeExecutionContractError(
                "network access requires a separately reviewed execution contract"
            )
        return self

    @property
    def manifest_sha256(self) -> str:
        """Digest every execution-affecting field; grants must bind to this digest."""
        self.validate()
        manifest = {
            "execution_id": self.execution_id,
            "actor_id": self.actor_id,
            "session_id": self.session_id,
            "task_id": self.task_id,
            "source_sha256": hashlib.sha256(self.source.encode("utf-8")).hexdigest(),
            "tool_names": list(self.tool_names),
            "timeout_seconds": self.timeout_seconds,
            "max_tool_calls": self.max_tool_calls,
            "max_result_bytes": self.max_result_bytes,
            "network_access": self.network_access,
        }
        encoded = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True, slots=True)
class ExecutionGrant:
    """Opaque signed authorization claims; fields alone are not proof of trust.

    The token is intentionally excluded from repr. Only a trusted verifier may
    establish authenticity, expiry, revocation, and actor/session binding.
    """

    actor_id: str
    session_id: str
    task_id: str
    execution_id: str
    manifest_sha256: str
    allowed_tools: tuple[str, ...]
    expires_at: str
    token: str = field(repr=False)

    def validate_for(self, request: CodeExecutionRequest, *, now: datetime | None = None) -> None:
        request.validate()
        if (
            self.actor_id != request.actor_id
            or self.session_id != request.session_id
            or self.task_id != request.task_id
            or self.execution_id != request.execution_id
            or not isinstance(self.manifest_sha256, str)
            or not re.fullmatch(r"[0-9a-f]{64}", self.manifest_sha256)
            or self.manifest_sha256 != request.manifest_sha256
        ):
            raise CodeExecutionContractError("execution grant does not match the exact request")
        if (
            not isinstance(self.allowed_tools, tuple)
            or len(self.allowed_tools) > _MAX_TOOLS
            or any(not isinstance(name, str) or not _TOOL.fullmatch(name) for name in self.allowed_tools)
        ):
            raise CodeExecutionContractError("execution grant tool list is invalid")
        if len(set(self.allowed_tools)) != len(self.allowed_tools) or not set(request.tool_names) <= set(self.allowed_tools):
            raise CodeExecutionContractError("execution grant does not authorize the requested tools")
        if not isinstance(self.token, str) or not self.token or len(self.token) > 4096:
            raise CodeExecutionContractError("execution grant token is invalid")
        try:
            expiry = datetime.fromisoformat(self.expires_at.replace("Z", "+00:00"))
        except (AttributeError, TypeError, ValueError) as exc:
            raise CodeExecutionContractError("execution grant expiry is invalid") from exc
        if expiry.tzinfo is None or expiry.utcoffset() is None:
            raise CodeExecutionContractError("execution grant expiry must include a timezone")
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None or current.utcoffset() is None:
            raise CodeExecutionContractError("current time must include a timezone")
        if expiry <= current:
            raise CodeExecutionContractError("execution grant has expired")


@dataclass(frozen=True, slots=True)
class CodeExecutionResult:
    """Bounded untrusted output returned by an executor."""

    status: str
    output: str
    tool_calls: int
    duration_seconds: float
    output_truncated: bool = False

    def validate(self, request: CodeExecutionRequest) -> "CodeExecutionResult":
        request.validate()
        if self.status not in {"succeeded", "failed", "cancelled", "timed_out"}:
            raise CodeExecutionContractError("executor returned an unknown status")
        if not isinstance(self.output, str):
            raise CodeExecutionContractError("executor output must be text")
        try:
            output_size = len(self.output.encode("utf-8", errors="strict"))
        except UnicodeError as exc:
            raise CodeExecutionContractError("executor output contains invalid Unicode") from exc
        if output_size > request.max_result_bytes:
            raise CodeExecutionContractError("executor output exceeds the requested byte limit")
        if type(self.tool_calls) is not int or not 0 <= self.tool_calls <= request.max_tool_calls:
            raise CodeExecutionContractError("executor tool-call count exceeds the requested limit")
        if (
            isinstance(self.duration_seconds, bool)
            or not isinstance(self.duration_seconds, (int, float))
            or self.duration_seconds < 0
            or self.duration_seconds > request.timeout_seconds + 5
        ):
            raise CodeExecutionContractError("executor duration is outside the permitted bound")
        if type(self.output_truncated) is not bool:
            raise CodeExecutionContractError("output_truncated must be a boolean")
        return self


ToolDispatcher = Callable[[str, Mapping[str, object]], Mapping[str, object]]
CancellationCheck = Callable[[], bool]


@runtime_checkable
class CodeExecutor(Protocol):
    """Backend contract; implementations must not bypass Yuki's host-side policy."""

    def execute(
        self,
        request: CodeExecutionRequest,
        grant: ExecutionGrant,
        *,
        dispatch_tool: ToolDispatcher,
        cancellation_requested: CancellationCheck,
    ) -> CodeExecutionResult:
        """Execute one exact request and return bounded, untrusted output."""
        ...


@runtime_checkable
class ExecutionGrantVerifier(Protocol):
    """Trusted control-plane verifier for signed, revocable, short-lived grants."""

    def verify(self, grant: ExecutionGrant, request: CodeExecutionRequest) -> bool:
        """Return True only for an authentic, unexpired, unrevoked exact grant."""
        ...
