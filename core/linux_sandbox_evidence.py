"""Combined diagnostic evidence report; never an execution attestation."""
from __future__ import annotations

import time
from dataclasses import dataclass

from core.cgroup_evidence import CgroupV2Limits, inspect_cgroup_v2_limits
from core.linux_host_evidence import LinuxProcessSnapshot, collect_linux_process_snapshot
from core.sandbox_runtime import SandboxRuntimeError


@dataclass(frozen=True)
class LinuxSandboxEvidenceReport:
    """Versioned observations collected in sequence, not an atomic host snapshot."""

    schema_version: int
    evidence_class: str
    observation_started_unix_ns: int
    observation_finished_unix_ns: int
    collection_duration_ns: int
    process: LinuxProcessSnapshot
    cgroup: CgroupV2Limits
    unknown_controls: tuple[str, ...]
    attested: bool = False
    verified: bool = False


def collect_linux_sandbox_evidence(
    pid: int,
    *,
    proc_root: str = "/proc",
    cgroup_root: str = "/sys/fs/cgroup",
) -> LinuxSandboxEvidenceReport:
    """Collect process and cgroup diagnostics and bind them by reported membership.

    The procfs and cgroup roots are supplied paths, not trusted mount attestations.
    The observations happen sequentially and may race with workload lifecycle or
    cgroup migration. This report must never authorize execution.
    """
    started_wall = time.time_ns()
    started_mono = time.monotonic_ns()
    process = collect_linux_process_snapshot(pid, proc_root=proc_root)
    if process.attested:
        raise SandboxRuntimeError("process collector returned an unexpected attestation")
    if process.cgroup_v2_path is None:
        raise SandboxRuntimeError("process has no unambiguous cgroup v2 membership")
    cgroup = inspect_cgroup_v2_limits(process.cgroup_v2_path, cgroup_root=cgroup_root)
    finished_mono = time.monotonic_ns()
    finished_wall = time.time_ns()

    if cgroup.cgroup_path != process.cgroup_v2_path:
        raise SandboxRuntimeError("process and cgroup observations disagree on membership")
    if cgroup.attested:
        raise SandboxRuntimeError("cgroup observer returned an unexpected attestation")
    if finished_mono < started_mono or finished_wall < started_wall:
        raise SandboxRuntimeError("evidence observation clock moved backwards")

    unknown = tuple(dict.fromkeys(process.unknown_controls + cgroup.unknown_controls + (
        "procfs and cgroup roots are not independently verified host mounts",
        "process membership may change between sequential observations",
        "observations are not an atomic snapshot and may be stale immediately",
        "namespace identities are not compared with a trusted host baseline",
        "cgroup controller enablement and effective kernel enforcement are unverified",
        "mount, network, LSM, runtime socket, and device exposure are not inspected",
    )))
    return LinuxSandboxEvidenceReport(
        schema_version=1,
        evidence_class="diagnostic_only",
        observation_started_unix_ns=started_wall,
        observation_finished_unix_ns=finished_wall,
        collection_duration_ns=finished_mono - started_mono,
        process=process,
        cgroup=cgroup,
        unknown_controls=unknown,
        attested=False,
        verified=False,
    )


# The diagnostic report above intentionally remains non-attesting. The builder
# below is a separate operator-injected evidence source used only after host
# deployment supplies approved roots and a trusted runtime probe.
import hashlib as _hashlib
import os as _os
import re as _re
import stat as _stat
from datetime import datetime as _datetime, timedelta as _timedelta, timezone as _timezone
from pathlib import Path as _Path
from typing import Callable as _Callable, Mapping as _Mapping

from automation.agent_execution_spec import AgentExecutionSpec as _AgentExecutionSpec
from core.host_runtime_inventory import RuntimeInventoryError as _RuntimeInventoryError
from core.runtime_environment_readiness import RuntimeEnvironmentEvidence as _RuntimeEnvironmentEvidence
from core.sandbox_executor import DockerSandboxExecutor as _DockerSandboxExecutor
from core.sandbox_executor import SandboxExecutionProfile as _SandboxExecutionProfile

_HOST_COMPONENT = _re.compile(r"^[A-Za-z0-9_.-]{1,128}$")
_HOST_CONTAINER_ID = _re.compile(r"^[0-9a-f]{64}$")
_HOST_VERIFIER = _re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,119}$")


def _host_canonical_path(path: _Path) -> _Path:
    if not path.is_absolute():
        raise _RuntimeInventoryError("host paths must be absolute.")
    cursor = _Path(path.anchor)
    for part in path.parts[1:]:
        cursor = cursor / part
        try:
            info = _os.lstat(cursor)
        except OSError as exc:
            raise _RuntimeInventoryError("required host path is unavailable.") from exc
        if _stat.S_ISLNK(info.st_mode):
            raise _RuntimeInventoryError("host path contains a symlink.")
    try:
        resolved = path.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise _RuntimeInventoryError("host path cannot be resolved.") from exc
    if resolved != path:
        raise _RuntimeInventoryError("host path is not canonical.")
    return resolved


def _host_read_bounded(path: _Path, maximum: int = 4096) -> str:
    fd = None
    try:
        fd = _os.open(path, _os.O_RDONLY | getattr(_os, "O_NOFOLLOW", 0) | getattr(_os, "O_CLOEXEC", 0))
        if not _stat.S_ISREG(_os.fstat(fd).st_mode):
            raise _RuntimeInventoryError("host evidence source is not a regular file.")
        data = bytearray()
        while len(data) <= maximum:
            chunk = _os.read(fd, min(1024, maximum + 1 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
    except _RuntimeInventoryError:
        raise
    except OSError as exc:
        raise _RuntimeInventoryError("required host evidence source is unavailable.") from exc
    finally:
        if fd is not None:
            _os.close(fd)
    if len(data) > maximum:
        raise _RuntimeInventoryError("host evidence source exceeded its byte ceiling.")
    try:
        return data.decode("ascii", errors="strict").strip()
    except UnicodeError as exc:
        raise _RuntimeInventoryError("host evidence source is not valid ASCII.") from exc


class LinuxSandboxEvidenceBuilder:
    """Build bounded runtime evidence from host cgroups, mounts and a trusted probe.

    runtime_probe is an operator-controlled, fixed host-agent operation. It
    receives only the daemon container ID and pinned image reference. It must
    not execute task-supplied commands or import task-controlled modules.
    """

    def __init__(
        self, *, approved_workspace_root: str,
        runtime_probe: _Callable[[str, str], _Mapping[str, _Mapping[str, str]]],
        verifier_id: str, proc_root: str = "/proc",
        cgroup_root: str = "/sys/fs/cgroup", evidence_ttl_seconds: int = 120,
    ) -> None:
        if _os.name != "posix" or not hasattr(_os, "O_TMPFILE"):
            raise RuntimeError("trusted sandbox evidence requires Linux filesystem primitives.")
        if type(approved_workspace_root) is not str or not _os.path.isabs(approved_workspace_root):
            raise ValueError("approved_workspace_root must be an absolute path.")
        if not callable(runtime_probe):
            raise TypeError("runtime_probe must be an operator-trusted callable.")
        if type(verifier_id) is not str or not _HOST_VERIFIER.fullmatch(verifier_id):
            raise ValueError("verifier_id is invalid.")
        if type(evidence_ttl_seconds) is not int or not 1 <= evidence_ttl_seconds <= 300:
            raise ValueError("evidence_ttl_seconds must be between 1 and 300.")
        self.approved_root = _host_canonical_path(_Path(approved_workspace_root))
        self.proc_root = _Path(proc_root)
        self.cgroup_root = _host_canonical_path(_Path(cgroup_root))
        self.runtime_probe = runtime_probe
        self.verifier_id = verifier_id
        self.ttl = evidence_ttl_seconds
        self.root_id = _hashlib.sha256(str(self.approved_root).encode("utf-8")).hexdigest()
        self._active_policy_capabilities = frozenset()

    def _cgroup(self, pid: int) -> _Path:
        if type(pid) is not int or pid <= 1:
            raise _RuntimeInventoryError("Docker reported an invalid host PID.")
        proc_entry = self.proc_root / str(pid)
        try:
            if _stat.S_ISLNK(_os.lstat(proc_entry).st_mode):
                raise _RuntimeInventoryError("container proc entry is a symlink.")
        except OSError as exc:
            raise _RuntimeInventoryError("container proc entry is unavailable.") from exc
        lines = _host_read_bounded(proc_entry / "cgroup", 16384).splitlines()
        entries = [line for line in lines if line.startswith("0::")]
        if len(entries) != 1:
            raise _RuntimeInventoryError("container lacks one unambiguous cgroup-v2 membership.")
        raw = entries[0][3:]
        if not raw.startswith("/") or "//" in raw or "\\x00" in raw:
            raise _RuntimeInventoryError("container cgroup path is malformed.")
        parts = raw.strip("/").split("/") if raw.strip("/") else []
        if any(not _HOST_COMPONENT.fullmatch(part) or part in (".", "..") for part in parts):
            raise _RuntimeInventoryError("container cgroup path contains unsafe components.")
        cursor = self.cgroup_root
        for part in parts:
            cursor = cursor / part
            try:
                if _stat.S_ISLNK(_os.lstat(cursor).st_mode):
                    raise _RuntimeInventoryError("container cgroup path contains a symlink.")
            except OSError as exc:
                raise _RuntimeInventoryError("container cgroup path is unavailable.") from exc
        try:
            resolved = cursor.resolve(strict=True)
            resolved.relative_to(self.cgroup_root)
        except (OSError, RuntimeError, ValueError) as exc:
            raise _RuntimeInventoryError("container cgroup escapes the configured cgroup root.") from exc
        return resolved

    def _limits(self, cgroup: _Path, profile: _SandboxExecutionProfile) -> tuple[int, int]:
        cpu = _host_read_bounded(cgroup / "cpu.max").split()
        if len(cpu) != 2 or cpu[0] == "max":
            raise _RuntimeInventoryError("container CPU quota is not finite.")
        try:
            quota, period = int(cpu[0]), int(cpu[1])
            memory_bytes = int(_host_read_bounded(cgroup / "memory.max"))
            pids = int(_host_read_bounded(cgroup / "pids.max"))
        except ValueError as exc:
            raise _RuntimeInventoryError("container cgroup limits are malformed or unlimited.") from exc
        if min(quota, period, memory_bytes, pids) <= 0:
            raise _RuntimeInventoryError("container cgroup limits must be positive and finite.")
        cpu_millis = quota * 1000 // period
        memory_mb = memory_bytes // (1024 * 1024)
        if (quota * 1000 != profile.policy.cpu_millis * period
                or memory_bytes != profile.policy.memory_mb * 1024 * 1024
                or pids != profile.policy.max_processes):
            raise _RuntimeInventoryError("effective cgroup limits differ from sandbox policy.")
        return cpu_millis, memory_mb

    def _workspace(self, container: _Mapping[str, object]) -> tuple[_Path, bool]:
        mounts = container.get("Mounts")
        if not isinstance(mounts, list) or len(mounts) > 128:
            raise _RuntimeInventoryError("Docker mount inventory is invalid.")
        matches = [item for item in mounts if isinstance(item, dict) and item.get("Destination") == "/workspace"]
        if len(matches) != 1:
            raise _RuntimeInventoryError("container must have exactly one /workspace mount.")
        mount = matches[0]
        source = mount.get("Source")
        if mount.get("Type") != "bind" or type(source) is not str or not _os.path.isabs(source):
            raise _RuntimeInventoryError("workspace must be an absolute host bind mount.")
        path = _host_canonical_path(_Path(source))
        try:
            path.relative_to(self.approved_root)
        except ValueError as exc:
            raise _RuntimeInventoryError("workspace mount escapes the approved root.") from exc
        if path == self.approved_root or not path.is_dir():
            raise _RuntimeInventoryError("workspace must be a child directory of the approved root.")
        expected_rw = "write_workspace" in self._active_policy_capabilities
        if mount.get("RW") is not expected_rw:
            raise _RuntimeInventoryError("workspace mount mode differs from the sandbox policy.")
        return path, _os.access(path, _os.W_OK)

    def __call__(
        self, task_id: str, worker_id: str, container_id: str,
        container: _Mapping[str, object], spec: _AgentExecutionSpec,
        profile: _SandboxExecutionProfile,
    ) -> _RuntimeEnvironmentEvidence:
        if type(container_id) is not str or not _HOST_CONTAINER_ID.fullmatch(container_id):
            raise _RuntimeInventoryError("a full daemon-assigned container ID is required.")
        state = container.get("State")
        if not isinstance(state, dict) or state.get("Running") is not True:
            raise _RuntimeInventoryError("container is not running.")
        cgroup = self._cgroup(state.get("Pid"))
        cpu_millis, memory_mb = self._limits(cgroup, profile)
        self._active_policy_capabilities = profile.policy.capabilities
        workspace, writable = self._workspace(container)
        fs = _os.statvfs(workspace)
        disk_free_mb = fs.f_bavail * fs.f_frsize // (1024 * 1024)
        if disk_free_mb < 1:
            raise _RuntimeInventoryError("workspace free disk space cannot be established.")
        inventory = self.runtime_probe(container_id, profile.image)
        if not isinstance(inventory, _Mapping) or set(inventory) != {"runtime_versions", "installed_dependencies"}:
            raise _RuntimeInventoryError("trusted runtime probe returned an invalid inventory shape.")
        runtimes, packages = inventory["runtime_versions"], inventory["installed_dependencies"]
        if not isinstance(runtimes, _Mapping) or not isinstance(packages, _Mapping):
            raise _RuntimeInventoryError("trusted runtime probe returned invalid version maps.")
        now = _datetime.now(_timezone.utc)
        return _RuntimeEnvironmentEvidence(
            task_id=task_id, worker_id=worker_id, spec_id=spec.spec_id,
            sandbox_image=profile.image, captured_at=now,
            expires_at=now + _timedelta(seconds=self.ttl), verifier_id=self.verifier_id,
            runtime_versions=runtimes, installed_dependencies=packages,
            cpu_millis=cpu_millis, memory_mb=memory_mb, disk_free_mb=disk_free_mb,
            workspace_root_id=self.root_id, approved_workspace_root_id=self.root_id,
            workspace_within_approved_root=True, workspace_symlink_free=True,
            workspace_writable=writable,
            sandbox_policy_sha256=_DockerSandboxExecutor._policy_digest(profile.policy),
        )
