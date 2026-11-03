"""Fail-closed preparation gate for supervised sandboxed evolution work.

This module validates approval and execution boundaries. It deliberately does
not launch Docker or execute generated code; a separately audited runtime must
enforce the returned SandboxExecutionSpec.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from pathlib import Path
from typing import Callable, Sequence

from core.sandbox_policy import SandboxPolicy, SandboxPolicyError

_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,159}$")
_SHA256 = re.compile(r"^[a-f0-9]{64}$")
_IMAGE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/:@-]{0,254}$")
_MAX_ARGS = 64
_MAX_ARG_LENGTH = 4096


class EvolutionExecutionGateError(RuntimeError):
    """Raised when an evolution execution request fails closed."""


@dataclass(frozen=True)
class SandboxExecutionSpec:
    plan_id: str
    request_id: str
    plan_digest: str
    actor_id: str
    image: str
    workspace: str
    command: tuple[str, ...]
    policy: SandboxPolicy


class EvolutionExecutionGate:
    """Prepare an immutable sandbox request only after approval verification.

    The approval verifier must check an authenticated, durable approval bound
    to this exact plan ID and digest. A truthy value is insufficient: it must
    return the literal boolean True. The workspace is constrained to a
    configured root and the sandbox policy is validated before a spec exists.
    """

    def __init__(
        self,
        *,
        workspace_root: str | Path,
        approval_verifier: Callable[..., bool],
    ):
        if not callable(approval_verifier):
            raise ValueError("approval_verifier must be callable.")
        try:
            root = Path(workspace_root).expanduser().resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise ValueError("workspace_root must be an existing directory.") from exc
        if not root.is_dir():
            raise ValueError("workspace_root must be an existing directory.")
        self.workspace_root = root
        self.approval_verifier = approval_verifier

    @staticmethod
    def _identifier(value: str, name: str) -> str:
        if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
            raise EvolutionExecutionGateError(f"{name} is invalid.")
        return value

    def prepare(
        self,
        *,
        plan_id: str,
        request_id: str,
        plan_digest: str,
        actor_id: str,
        image: str,
        workspace: str | Path,
        command: Sequence[str],
        policy: SandboxPolicy,
    ) -> SandboxExecutionSpec:
        self._identifier(plan_id, "plan_id")
        self._identifier(request_id, "request_id")
        if not isinstance(plan_digest, str) or not _SHA256.fullmatch(plan_digest):
            raise EvolutionExecutionGateError("plan_digest must be a SHA-256 hex digest.")
        if not isinstance(actor_id, str) or not actor_id.strip() or len(actor_id) > 256:
            raise EvolutionExecutionGateError("actor_id is invalid.")
        if any(ord(ch) < 32 for ch in actor_id):
            raise EvolutionExecutionGateError("actor_id contains control characters.")
        if not isinstance(image, str) or not _IMAGE.fullmatch(image):
            raise EvolutionExecutionGateError("sandbox image reference is invalid.")
        if not isinstance(command, (list, tuple)) or not 1 <= len(command) <= _MAX_ARGS:
            raise EvolutionExecutionGateError("command must contain 1-64 arguments.")
        normalized_command = []
        for arg in command:
            if not isinstance(arg, str) or not arg or "\x00" in arg or len(arg) > _MAX_ARG_LENGTH:
                raise EvolutionExecutionGateError("command arguments must be bounded non-empty strings.")
            normalized_command.append(arg)
        if not isinstance(policy, SandboxPolicy):
            raise EvolutionExecutionGateError("a SandboxPolicy is required.")
        try:
            policy.validate()
        except (AttributeError, SandboxPolicyError) as exc:
            raise EvolutionExecutionGateError("sandbox policy is invalid.") from exc
        if policy.network_mode != "none":
            raise EvolutionExecutionGateError("evolution execution requires networking disabled.")
        if "write_workspace" not in policy.capabilities:
            raise EvolutionExecutionGateError("evolution execution requires explicit workspace-write capability.")

        try:
            resolved = Path(workspace).expanduser().resolve(strict=True)
            contained = resolved != self.workspace_root and resolved.is_relative_to(self.workspace_root)
            if not resolved.is_dir() or not contained:
                raise EvolutionExecutionGateError("workspace must be a child directory of workspace_root.")
        except (OSError, RuntimeError, TypeError) as exc:
            raise EvolutionExecutionGateError("workspace cannot be resolved safely.") from exc

        try:
            approved = self.approval_verifier(
                plan_id=plan_id,
                plan_digest=plan_digest,
                request_id=request_id,
                actor_id=actor_id,
            )
        except Exception as exc:
            raise EvolutionExecutionGateError("approval verification failed closed.") from exc
        if approved is not True:
            raise EvolutionExecutionGateError("exact-plan approval is required.")

        return SandboxExecutionSpec(
            plan_id=plan_id,
            request_id=request_id,
            plan_digest=plan_digest,
            actor_id=actor_id,
            image=image,
            workspace=str(resolved),
            command=tuple(normalized_command),
            policy=policy,
        )
