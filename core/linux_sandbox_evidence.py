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
