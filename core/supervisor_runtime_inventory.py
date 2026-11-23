"""Typed supervisor inventory snapshots and a fail-closed evidence adapter.

The supervisor callback is the only source of runtime facts. It must query the
container control plane and host filesystem from outside task code; this module
validates the returned shape and binds it to the requested task, worker session,
immutable image digest and effective sandbox policy before accepting it.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Callable

from automation.agent_execution_spec import AgentExecutionSpec
from core.host_runtime_inventory import RuntimeInventoryError
from core.runtime_environment_readiness import RuntimeEnvironmentEvidence
from core.sandbox_executor import SandboxExecutionProfile, DockerSandboxExecutor

_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}$")
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class SupervisorRuntimeSnapshot:
    """Supervisor-observed identity plus the measured runtime inventory."""

    evidence: RuntimeEnvironmentEvidence
    host_id: str
    container_id: str
    worker_session_id: str
    observed_image_digest: str
    observed_policy_sha256: str

    def __post_init__(self) -> None:
        if not isinstance(self.evidence, RuntimeEnvironmentEvidence):
            raise TypeError("evidence must be RuntimeEnvironmentEvidence.")
        for field in ("host_id", "container_id", "worker_session_id"):
            value = getattr(self, field)
            if type(value) is not str or not _ID.fullmatch(value):
                raise ValueError(f"{field} must be a bounded supervisor identifier.")
        if type(self.observed_image_digest) is not str or not _DIGEST.fullmatch(self.observed_image_digest):
            raise ValueError("observed_image_digest must be an immutable sha256 digest.")
        if type(self.observed_policy_sha256) is not str or not re.fullmatch(r"[0-9a-f]{64}", self.observed_policy_sha256):
            raise ValueError("observed_policy_sha256 must be a lowercase SHA-256 digest.")


@dataclass(frozen=True, slots=True)
class SupervisorRuntimeInventoryAdapter:
    """Convert authenticated control-plane snapshots into readiness evidence."""

    collector: Callable[[str, str, str | None, AgentExecutionSpec, SandboxExecutionProfile], SupervisorRuntimeSnapshot]
    verifier: Callable[[SupervisorRuntimeSnapshot, str, AgentExecutionSpec, SandboxExecutionProfile], bool]

    def __post_init__(self) -> None:
        if not callable(self.collector) or not callable(self.verifier):
            raise TypeError("supervisor collector and verifier must be callable.")

    def collect(
        self, *, task_id: str, worker_id: str, spec: AgentExecutionSpec,
        profile: SandboxExecutionProfile,
        expected_container_id: str | None = None,
    ) -> RuntimeEnvironmentEvidence:
        for name, value, limit in (("task_id", task_id, 128), ("worker_id", worker_id, 200)):
            if type(value) is not str or not value.strip() or len(value) > limit or any(ord(c) < 32 for c in value):
                raise ValueError(f"{name} must be bounded non-empty text.")
        if not isinstance(spec, AgentExecutionSpec) or not isinstance(profile, SandboxExecutionProfile):
            raise TypeError("pinned execution spec and sandbox profile are required.")
        if profile.spec_id != spec.spec_id:
            raise RuntimeInventoryError("sandbox profile does not match the pinned execution spec.")
        try:
            snapshot = self.collector(task_id, worker_id, expected_container_id, spec, profile)
        except Exception as exc:
            raise RuntimeInventoryError("supervisor runtime snapshot collection failed.") from exc
        if not isinstance(snapshot, SupervisorRuntimeSnapshot):
            raise RuntimeInventoryError("supervisor returned an untyped runtime snapshot.")
        if expected_container_id is not None and (
            type(expected_container_id) is not str or not _ID.fullmatch(expected_container_id)
            or snapshot.container_id != expected_container_id
        ):
            raise RuntimeInventoryError("supervisor snapshot does not identify the live sandbox container from the lifecycle event.")
        evidence = snapshot.evidence
        expected_image_digest = profile.image.rsplit("@", 1)[-1]
        expected_policy_digest = DockerSandboxExecutor._policy_digest(profile.policy)
        if (
            evidence.task_id != task_id
            or evidence.spec_id != spec.spec_id
            or evidence.worker_id != worker_id
            or evidence.sandbox_image != profile.image
            or snapshot.worker_session_id != worker_id
            or snapshot.observed_image_digest != expected_image_digest
            or snapshot.observed_policy_sha256 != expected_policy_digest
            or evidence.sandbox_policy_sha256 != expected_policy_digest
        ):
            raise RuntimeInventoryError("supervisor snapshot identity, image digest or effective policy does not match the pinned execution.")
        try:
            verified = self.verifier(snapshot, worker_id, spec, profile) is True
        except Exception as exc:
            raise RuntimeInventoryError("supervisor snapshot authentication failed.") from exc
        if not verified:
            raise RuntimeInventoryError("supervisor snapshot authentication failed.")
        return evidence
