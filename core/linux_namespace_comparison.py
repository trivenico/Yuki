"""Compare Linux namespace observations without asserting baseline trust."""
from __future__ import annotations

from dataclasses import dataclass

from core.linux_host_evidence import LinuxProcessSnapshot
from core.sandbox_runtime import SandboxRuntimeError

_REQUIRED = ("user", "pid", "mnt", "ipc", "net")


@dataclass(frozen=True)
class NamespaceComparison:
    namespace: str
    baseline_identity: str
    workload_identity: str
    same_identity: bool


@dataclass(frozen=True)
class LinuxNamespaceComparison:
    schema_version: int
    baseline_pid: int
    workload_pid: int
    comparisons: tuple[NamespaceComparison, ...]
    shared_namespace_types: tuple[str, ...]
    distinct_namespace_types: tuple[str, ...]
    evidence_class: str
    unknown_controls: tuple[str, ...]
    attested: bool = False
    verified: bool = False


def _namespace_map(snapshot: LinuxProcessSnapshot) -> dict[str, str]:
    if not isinstance(snapshot, LinuxProcessSnapshot):
        raise SandboxRuntimeError("namespace comparison requires process snapshots")
    values = dict(snapshot.namespace_links)
    if len(values) != len(snapshot.namespace_links) or set(values) != set(_REQUIRED):
        raise SandboxRuntimeError("process snapshot has incomplete or duplicate namespace identities")
    if any(type(value) is not str or not value for value in values.values()):
        raise SandboxRuntimeError("process snapshot contains invalid namespace identity")
    return values


def compare_linux_namespaces(
    baseline: LinuxProcessSnapshot,
    workload: LinuxProcessSnapshot,
) -> LinuxNamespaceComparison:
    """Compare observed namespace inode labels; equal means shared, not isolated."""
    if baseline.pid == workload.pid and baseline.start_time_ticks == workload.start_time_ticks:
        raise SandboxRuntimeError("baseline and workload identify the same process")
    baseline_map = _namespace_map(baseline)
    workload_map = _namespace_map(workload)
    comparisons = tuple(
        NamespaceComparison(
            namespace=name,
            baseline_identity=baseline_map[name],
            workload_identity=workload_map[name],
            same_identity=baseline_map[name] == workload_map[name],
        )
        for name in _REQUIRED
    )
    shared = tuple(item.namespace for item in comparisons if item.same_identity)
    distinct = tuple(item.namespace for item in comparisons if not item.same_identity)
    return LinuxNamespaceComparison(
        schema_version=1,
        baseline_pid=baseline.pid,
        workload_pid=workload.pid,
        comparisons=comparisons,
        shared_namespace_types=shared,
        distinct_namespace_types=distinct,
        evidence_class="diagnostic_comparison",
        unknown_controls=(
            "baseline process identity and host authority are not independently verified",
            "namespace inode labels are observations from caller-selected procfs",
            "distinct namespace identity does not prove effective isolation or policy correctness",
            "shared namespace identity may expose host or peer resources",
            "user namespace UID/GID mappings are not inspected",
            "namespace membership may change immediately after observation",
            "mount, network, LSM, seccomp, cgroup, socket, and device controls require separate verification",
        ),
        attested=False,
        verified=False,
    )
