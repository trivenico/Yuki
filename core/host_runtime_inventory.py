"""Host-injected runtime inventory provider with worker-session binding.

This adapter deliberately does not inspect the host by running shell commands or
importing task packages. Deployments inject a supervisor-owned collector that
reads the worker/container runtime through its trusted control plane, and a
separate verifier that authenticates the resulting snapshot.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from automation.agent_execution_spec import AgentExecutionSpec
from core.runtime_environment_readiness import RuntimeEnvironmentEvidence
from core.sandbox_executor import SandboxExecutionProfile


class RuntimeInventoryError(RuntimeError):
    """Trusted runtime inventory could not be collected or authenticated."""


@dataclass(frozen=True, slots=True)
class HostRuntimeInventoryProvider:
    """Bridge from a trusted supervisor to immutable readiness evidence.

    The collector must inspect the target worker from outside task code and
    return a task/spec/worker-bound snapshot. The verifier must authenticate
    provenance, worker process session, host, container identity, image digest,
    and the effective sandbox policy. Neither callback has a permissive default.
    """

    collector: Callable[[str, str, AgentExecutionSpec, SandboxExecutionProfile], RuntimeEnvironmentEvidence]
    verifier: Callable[[RuntimeEnvironmentEvidence, str, AgentExecutionSpec, SandboxExecutionProfile], bool]

    def __post_init__(self) -> None:
        if not callable(self.collector) or not callable(self.verifier):
            raise TypeError("host inventory collector and verifier must be callable.")

    def collect(
        self,
        *,
        task_id: str,
        worker_id: str,
        spec: AgentExecutionSpec,
        profile: SandboxExecutionProfile,
    ) -> RuntimeEnvironmentEvidence:
        for name, value, limit in (
            ("task_id", task_id, 128), ("worker_id", worker_id, 200),
        ):
            if type(value) is not str or not value.strip() or len(value) > limit or any(ord(c) < 32 for c in value):
                raise ValueError(f"{name} must be bounded non-empty text.")
        if not isinstance(spec, AgentExecutionSpec) or not isinstance(profile, SandboxExecutionProfile):
            raise TypeError("pinned execution spec and sandbox profile are required.")
        if profile.spec_id != spec.spec_id:
            raise RuntimeInventoryError("sandbox profile does not match the pinned execution spec.")
        try:
            evidence = self.collector(task_id, worker_id, spec, profile)
        except Exception as exc:
            raise RuntimeInventoryError("trusted host runtime inventory collection failed.") from exc
        if not isinstance(evidence, RuntimeEnvironmentEvidence):
            raise RuntimeInventoryError("host collector returned no valid runtime evidence.")
        if (
            evidence.task_id != task_id
            or evidence.spec_id != spec.spec_id
            or evidence.worker_id != worker_id
            or evidence.sandbox_image != profile.image
        ):
            raise RuntimeInventoryError("host runtime evidence is not bound to the requested task, worker, spec and image.")
        try:
            verified = self.verifier(evidence, worker_id, spec, profile) is True
        except Exception as exc:
            raise RuntimeInventoryError("host runtime evidence verification failed.") from exc
        if not verified:
            raise RuntimeInventoryError("host runtime evidence verification failed.")
        return evidence
