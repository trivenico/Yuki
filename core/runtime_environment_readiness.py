"""Host-attested runtime, dependency, workspace and resource compatibility checks."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import re
from typing import Callable, Mapping
from types import MappingProxyType

from automation.agent_execution_spec import AgentExecutionSpec
from core.sandbox_executor import SandboxExecutionProfile, DockerSandboxExecutor

_PACKAGE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9.+!_-]{0,63}$")
_RUNTIME = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _normalized_package(value: str) -> str:
    if type(value) is not str or not _PACKAGE.fullmatch(value):
        raise ValueError("dependency names must be bounded package identifiers.")
    return re.sub(r"[-_.]+", "-", value).lower()


@dataclass(frozen=True, slots=True)
class RuntimeEnvironmentEvidence:
    """A snapshot returned by the trusted host inventory agent, not by task code."""
    task_id: str
    spec_id: str
    sandbox_image: str
    captured_at: datetime
    expires_at: datetime
    verifier_id: str
    runtime_versions: Mapping[str, str]
    installed_dependencies: Mapping[str, str]
    cpu_millis: int
    memory_mb: int
    disk_free_mb: int
    workspace_root_id: str
    approved_workspace_root_id: str
    workspace_within_approved_root: bool
    workspace_symlink_free: bool
    workspace_writable: bool
    sandbox_policy_sha256: str
    worker_id: str = ""

    def __post_init__(self) -> None:
        for field in ("task_id", "spec_id", "verifier_id", "workspace_root_id", "approved_workspace_root_id"):
            value = getattr(self, field)
            if type(value) is not str or not value.strip() or len(value) > 128 or any(ord(c) < 32 for c in value):
                raise ValueError(f"{field} must be bounded non-empty text.")
        if type(self.worker_id) is not str or len(self.worker_id) > 200 or any(ord(c) < 32 for c in self.worker_id):
            raise ValueError("worker_id must be bounded text when supplied.")
        if type(self.sandbox_image) is not str or len(self.sandbox_image) > 256:
            raise ValueError("sandbox_image must be bounded text.")
        for field in ("captured_at", "expires_at"):
            value = getattr(self, field)
            if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
                raise ValueError(f"{field} must be timezone-aware.")
        for field, maximum in (("cpu_millis", 1_000_000), ("memory_mb", 1_048_576), ("disk_free_mb", 16_777_216)):
            value = getattr(self, field)
            if type(value) is not int or not 1 <= value <= maximum:
                raise ValueError(f"{field} is outside the permitted range.")
        for field in ("workspace_within_approved_root", "workspace_symlink_free", "workspace_writable"):
            if type(getattr(self, field)) is not bool:
                raise ValueError(f"{field} must be a boolean attestation.")
        runtime_versions = self.runtime_versions
        if not isinstance(runtime_versions, Mapping) or len(runtime_versions) > 128:
            raise ValueError("runtime_versions must be a bounded mapping.")
        for key, version in runtime_versions.items():
            if (type(key) is not str or not _RUNTIME.fullmatch(key)
                    or type(version) is not str or not _VERSION.fullmatch(version)):
                raise ValueError("runtime_versions contains an invalid runtime identifier or version.")
        object.__setattr__(self, "runtime_versions", MappingProxyType(dict(runtime_versions)))

        installed = self.installed_dependencies
        if not isinstance(installed, Mapping) or len(installed) > 4096:
            raise ValueError("installed_dependencies must be a bounded mapping.")
        # Import lazily to avoid coupling the evidence model to host IPC startup.
        from core.dependency_provenance import validate_installed_dependency_inventory
        try:
            canonical_installed = validate_installed_dependency_inventory(installed)
        except Exception as exc:
            raise ValueError("installed_dependencies contains invalid provenance identities or versions.") from exc
        object.__setattr__(self, "installed_dependencies", MappingProxyType(canonical_installed))
        if type(self.sandbox_policy_sha256) is not str or not _SHA256.fullmatch(self.sandbox_policy_sha256):
            raise ValueError("sandbox_policy_sha256 must be a lowercase SHA-256 digest.")


@dataclass(frozen=True, slots=True)
class RuntimeEnvironmentRequirements:
    """Operator-owned compatibility lock and minimum workspace/resource requirements."""
    runtime_versions: Mapping[str, str]
    locked_dependencies: Mapping[str, str]
    minimum_disk_mb: int = 256

    def __post_init__(self) -> None:
        if not isinstance(self.runtime_versions, Mapping) or len(self.runtime_versions) > 32:
            raise ValueError("runtime_versions must be a bounded mapping.")
        if not isinstance(self.locked_dependencies, Mapping) or len(self.locked_dependencies) > 128:
            raise ValueError("locked_dependencies must be a bounded mapping.")
        for name, version in self.runtime_versions.items():
            if type(name) is not str or not _RUNTIME.fullmatch(name) or type(version) is not str or not _VERSION.fullmatch(version):
                raise ValueError("runtime version requirements are invalid.")
        normalized = [_normalized_package(name) for name in self.locked_dependencies]
        if len(normalized) > 128 or len(set(normalized)) != len(normalized):
            raise ValueError("locked dependency names must be unique and bounded.")
        for version in self.locked_dependencies.values():
            if type(version) is not str or not _VERSION.fullmatch(version):
                raise ValueError("locked dependency versions are invalid.")
        if type(self.minimum_disk_mb) is not int or not 1 <= self.minimum_disk_mb <= 16_777_216:
            raise ValueError("minimum_disk_mb is outside the permitted range.")
        object.__setattr__(self, "runtime_versions", MappingProxyType(dict(self.runtime_versions)))
        object.__setattr__(self, "locked_dependencies", MappingProxyType(dict(self.locked_dependencies)))


@dataclass(frozen=True, slots=True)
class RuntimeEnvironmentReadiness:
    status: str
    ready_for_approval: bool
    reasons: tuple[str, ...]
    verifier_id: str | None
    captured_at: datetime | None


def assess_runtime_environment(
    *,
    spec: AgentExecutionSpec | None,
    profile: SandboxExecutionProfile | None,
    task_id: str,
    requirements: RuntimeEnvironmentRequirements | None,
    evidence: RuntimeEnvironmentEvidence | None,
    evidence_verifier: Callable[[RuntimeEnvironmentEvidence, AgentExecutionSpec, SandboxExecutionProfile], bool] | None,
    now: datetime | None = None,
    expected_worker_id: str | None = None,
) -> RuntimeEnvironmentReadiness:
    """Fail closed unless fresh host evidence matches the pinned spec and profile.

    evidence_verifier must validate provenance and bind the snapshot to this task,
    worker, host, sandbox and policy. It must return exactly True.
    """
    if spec is not None and not isinstance(spec, AgentExecutionSpec):
        raise TypeError("spec must be an AgentExecutionSpec or None.")
    if profile is not None and not isinstance(profile, SandboxExecutionProfile):
        raise TypeError("profile must be a SandboxExecutionProfile or None.")
    if requirements is not None and not isinstance(requirements, RuntimeEnvironmentRequirements):
        raise TypeError("requirements must be RuntimeEnvironmentRequirements or None.")
    if evidence is not None and not isinstance(evidence, RuntimeEnvironmentEvidence):
        raise TypeError("evidence must be RuntimeEnvironmentEvidence or None.")
    if expected_worker_id is not None and (type(expected_worker_id) is not str or not expected_worker_id.strip() or len(expected_worker_id) > 200):
        raise ValueError("expected_worker_id must be bounded non-empty text when supplied.")
    if type(task_id) is not str or not task_id.strip() or len(task_id) > 128:
        raise ValueError("task_id must be bounded non-empty text.")
    clock = now or datetime.now(timezone.utc)
    if not isinstance(clock, datetime) or clock.tzinfo is None or clock.utcoffset() is None:
        raise ValueError("now must be timezone-aware.")

    reasons: list[str] = []
    if spec is None: reasons.append("Pinned execution specification is unavailable.")
    if profile is None: reasons.append("Sandbox execution profile is unavailable.")
    if requirements is None: reasons.append("Operator runtime and dependency lock is unavailable.")
    if evidence is None: reasons.append("Host runtime environment evidence is unavailable.")
    if evidence_verifier is None or not callable(evidence_verifier):
        reasons.append("Trusted host evidence verifier is unavailable.")
    if spec is not None and profile is not None and profile.spec_id != spec.spec_id:
        reasons.append("Sandbox profile does not match the pinned execution specification.")

    if evidence is not None:
        if evidence.task_id != task_id: reasons.append("Host evidence is bound to a different task.")
        if expected_worker_id is not None and evidence.worker_id != expected_worker_id:
            reasons.append("Host evidence is bound to a different worker session.")
        if spec is not None and evidence.spec_id != spec.spec_id:
            reasons.append("Host evidence is bound to a different execution specification.")
        if profile is not None:
            if evidence.sandbox_image != profile.image:
                reasons.append("Host evidence sandbox image does not match the pinned image.")
            expected_digest = DockerSandboxExecutor._policy_digest(profile.policy)
            if evidence.sandbox_policy_sha256 != expected_digest:
                reasons.append("Host evidence sandbox policy digest does not match the configured policy.")
        if evidence.captured_at > clock or evidence.expires_at <= clock or evidence.expires_at <= evidence.captured_at:
            reasons.append("Host runtime evidence is expired or has invalid timestamps.")
        if (evidence.expires_at - evidence.captured_at).total_seconds() > 3600:
            reasons.append("Host runtime evidence validity exceeds the one-hour limit.")
        if evidence.workspace_root_id != evidence.approved_workspace_root_id or not evidence.workspace_within_approved_root or not evidence.workspace_symlink_free:
            reasons.append("Workspace is outside its approved root or contains an untrusted symlink.")
        if not evidence.workspace_writable:
            reasons.append("Workspace does not satisfy the configured write requirement.")

    if spec is not None and requirements is not None and evidence is not None:
        # AgentExecutionSpec.dependencies are execution-spec graph IDs, not package names.
        # Package pins are exclusively operator-owned in RuntimeEnvironmentRequirements.
        from core.dependency_provenance import validate_installed_dependency_inventory
        try:
            locked = validate_installed_dependency_inventory(requirements.locked_dependencies)
        except Exception:
            locked = {}
            reasons.append("Operator dependency lock contains invalid provenance identities.")
        installed = dict(evidence.installed_dependencies)
        for identity, version in locked.items():
            if installed.get(identity) != version:
                reasons.append(f"Dependency version mismatch or missing package: {identity}.")
        for name, version in requirements.runtime_versions.items():
            if evidence.runtime_versions.get(name) != version:
                reasons.append(f"Runtime version mismatch or missing runtime: {name}.")
        if profile is not None and evidence.cpu_millis < profile.policy.cpu_millis:
            reasons.append("Attested CPU capacity is below the sandbox policy requirement.")
        if evidence.memory_mb < spec.budget.max_memory_mb:
            reasons.append("Attested memory capacity is below the execution specification budget.")
        if evidence.disk_free_mb < requirements.minimum_disk_mb:
            reasons.append("Attested free disk capacity is below the configured minimum.")

    if evidence is not None and callable(evidence_verifier) and spec is not None and profile is not None:
        try:
            verified = evidence_verifier(evidence, spec, profile) is True
        except Exception:
            verified = False
        if not verified:
            reasons.append("Trusted host evidence verification failed.")

    if not reasons:
        return RuntimeEnvironmentReadiness("ready_for_approval", True, (), evidence.verifier_id, evidence.captured_at)
    review = any("unavailable" in reason for reason in reasons)
    return RuntimeEnvironmentReadiness(
        "review_required" if review else "blocked", False, tuple(reasons[:20]),
        evidence.verifier_id if evidence else None, evidence.captured_at if evidence else None,
    )
