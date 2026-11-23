"""Linux host-side sandbox boundary measurements.

This module observes cgroup v2 limits and Docker's bind-mounted workspace from
outside task code. It deliberately returns measurements, not an attestation;
the deployment's independent verifier must authenticate the observer and bind
its result to the live Docker container.
"""
from __future__ import annotations

import hashlib
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from core.host_runtime_inventory import RuntimeInventoryError

_PID = re.compile(r"^[1-9][0-9]{0,8}$")
_CGROUP_RELATIVE = re.compile(r"^/[A-Za-z0-9_.@/-]{1,1024}$")
_MAX_CGROUP_FILE = 4096


@dataclass(frozen=True, slots=True)
class LinuxHostBoundaryMeasurements:
    container_id: str
    process_id: int
    cgroup_path: str
    cpu_millis: int
    memory_mb: int
    disk_free_mb: int
    workspace_source: str
    workspace_target: str
    workspace_root_id: str
    approved_workspace_root_id: str
    workspace_within_approved_root: bool
    workspace_symlink_free: bool
    workspace_writable: bool


class LinuxHostBoundaryObserver:
    """Measure a container's effective cgroup v2 and workspace boundary.

    approved_workspace_root is an operator-configured host directory. proc_root
    and cgroup_root are injectable only to support isolated tests and alternate
    Linux mount layouts; production defaults are /proc and /sys/fs/cgroup.
    """

    def __init__(
        self,
        *,
        approved_workspace_root: str,
        proc_root: str = "/proc",
        cgroup_root: str = "/sys/fs/cgroup",
    ) -> None:
        for name, value in (
            ("approved_workspace_root", approved_workspace_root),
            ("proc_root", proc_root),
            ("cgroup_root", cgroup_root),
        ):
            if type(value) is not str or not os.path.isabs(value):
                raise ValueError(f"{name} must be an absolute operator-configured path.")
        self.approved_workspace_root = Path(approved_workspace_root)
        self.proc_root = Path(proc_root)
        self.cgroup_root = Path(cgroup_root)

    @staticmethod
    def _reject_symlink_components(path: Path) -> None:
        current = Path(path.anchor)
        for part in path.parts[1:]:
            current = current / part
            try:
                mode = current.lstat().st_mode
            except OSError as exc:
                raise RuntimeInventoryError("host boundary path is missing or unreadable.") from exc
            if stat.S_ISLNK(mode):
                raise RuntimeInventoryError("host boundary path contains a symlink.")

    @staticmethod
    def _read_bounded(path: Path) -> str:
        try:
            with path.open("rb") as stream:
                raw = stream.read(_MAX_CGROUP_FILE + 1)
        except OSError as exc:
            raise RuntimeInventoryError("effective cgroup measurement is unavailable.") from exc
        if len(raw) > _MAX_CGROUP_FILE:
            raise RuntimeInventoryError("cgroup measurement exceeded its byte ceiling.")
        try:
            return raw.decode("ascii").strip()
        except UnicodeDecodeError as exc:
            raise RuntimeInventoryError("cgroup measurement is not ASCII.") from exc

    def _container_cgroup(self, pid: int) -> tuple[Path, str]:
        proc_file = self.proc_root / str(pid) / "cgroup"
        text = self._read_bounded(proc_file)
        unified = [line[3:] for line in text.splitlines() if line.startswith("0::")]
        if len(unified) != 1 or not _CGROUP_RELATIVE.fullmatch(unified[0]):
            raise RuntimeInventoryError("container does not expose one valid unified cgroup v2 path.")
        relative = unified[0].lstrip("/")
        root = self.cgroup_root.resolve(strict=True)
        candidate = root.joinpath(*relative.split("/")) if relative else root
        self._reject_symlink_components(candidate)
        resolved = candidate.resolve(strict=True)
        if not resolved.is_relative_to(root) or resolved == root:
            raise RuntimeInventoryError("container cgroup path escapes the configured cgroup root.")
        return resolved, unified[0]

    def observe(
        self,
        *,
        container_inspect: Mapping[str, object],
        expected_container_id: str,
        approved_workspace_root: str | None = None,
    ) -> LinuxHostBoundaryMeasurements:
        if not isinstance(container_inspect, Mapping):
            raise TypeError("container_inspect must be a Docker inspect mapping.")
        container_id = container_inspect.get("Id")
        state = container_inspect.get("State")
        mounts = container_inspect.get("Mounts")
        if (
            type(container_id) is not str or not re.fullmatch(r"[0-9a-f]{64}", container_id)
            or not isinstance(state, Mapping) or state.get("Running") is not True
            or type(state.get("Pid")) is not int or state["Pid"] <= 0
            or not isinstance(mounts, list)
        ):
            raise RuntimeInventoryError("Docker inspect lacks a valid live container identity.")
        if type(expected_container_id) is not str or not re.fullmatch(r"[0-9a-f]{64}", expected_container_id) or container_id != expected_container_id:
            raise RuntimeInventoryError("host observer container ID does not match the inspected container.")
        pid = state["Pid"]
        if not _PID.fullmatch(str(pid)):
            raise RuntimeInventoryError("container host PID is invalid.")

        approved = Path(approved_workspace_root) if approved_workspace_root is not None else self.approved_workspace_root
        if not approved.is_absolute():
            raise RuntimeInventoryError("approved workspace root must be absolute.")
        self._reject_symlink_components(approved)
        approved_real = approved.resolve(strict=True)
        if not approved_real.is_dir():
            raise RuntimeInventoryError("approved workspace root is not a directory.")

        workspace_mounts = [
            item for item in mounts
            if isinstance(item, Mapping) and item.get("Destination") == "/workspace"
        ]
        if len(workspace_mounts) != 1:
            raise RuntimeInventoryError("container must have exactly one /workspace bind mount.")
        mount = workspace_mounts[0]
        source = mount.get("Source")
        mount_type = mount.get("Type")
        rw = mount.get("RW")
        if type(source) is not str or not os.path.isabs(source) or mount_type != "bind" or type(rw) is not bool:
            raise RuntimeInventoryError("workspace mount metadata is invalid.")
        source_path = Path(source)
        self._reject_symlink_components(source_path)
        source_real = source_path.resolve(strict=True)
        if not source_real.is_dir() or not source_real.is_relative_to(approved_real):
            raise RuntimeInventoryError("workspace bind source is outside the approved host root.")
        if source_real == approved_real:
            raise RuntimeInventoryError("workspace must not mount the entire approved host root.")
        try:
            mode = source_real.stat().st_mode
            writable = rw and os.access(source_real, os.W_OK) and bool(mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))
            disk_free_mb = os.statvfs(source_real).f_bavail * os.statvfs(source_real).f_frsize // (1024 * 1024)
        except OSError as exc:
            raise RuntimeInventoryError("workspace filesystem measurements are unavailable.") from exc
        if not 1 <= disk_free_mb <= 16_777_216:
            raise RuntimeInventoryError("workspace free disk measurement is outside supported bounds.")

        cgroup, cgroup_relative = self._container_cgroup(pid)
        # Parent cgroups can impose tighter limits than the container leaf.
        # Calculate the strictest finite ancestor limit rather than trusting
        # only the leaf's configured values.
        memory_limits: list[int] = []
        cpu_limits: list[tuple[int, int]] = []
        current = cgroup
        while current == cgroup or current.is_relative_to(self.cgroup_root.resolve(strict=True)):
            memory_text = self._read_bounded(current / "memory.max")
            cpu_text = self._read_bounded(current / "cpu.max")
            if memory_text != "max":
                try:
                    memory_value = int(memory_text)
                    if memory_value <= 0:
                        raise ValueError
                    memory_limits.append(memory_value)
                except ValueError as exc:
                    raise RuntimeInventoryError("cgroup memory limit is malformed.") from exc
            try:
                quota_text, period_text = cpu_text.split()
                if quota_text != "max":
                    quota_value, period_value = int(quota_text), int(period_text)
                    if quota_value <= 0 or period_value <= 0:
                        raise ValueError
                    cpu_limits.append((quota_value, period_value))
                elif int(period_text) <= 0:
                    raise ValueError
            except ValueError as exc:
                raise RuntimeInventoryError("cgroup CPU limit is malformed.") from exc
            if current == self.cgroup_root.resolve(strict=True):
                break
            current = current.parent
        if not memory_limits or not cpu_limits:
            raise RuntimeInventoryError("no finite effective cgroup v2 CPU and memory limits are available.")
        memory_bytes = min(memory_limits)
        cpu_quota, cpu_period = min(cpu_limits, key=lambda pair: pair[0] / pair[1])
        memory_mb = memory_bytes // (1024 * 1024)
        cpu_millis = cpu_quota * 1000 // cpu_period
        if not 1 <= memory_mb <= 1_048_576 or not 1 <= cpu_millis <= 1_000_000:
            raise RuntimeInventoryError("effective cgroup limits are outside supported bounds.")

        approved_id = hashlib.sha256(str(approved_real).encode("utf-8")).hexdigest()
        return LinuxHostBoundaryMeasurements(
            container_id=container_id, process_id=pid, cgroup_path=cgroup_relative,
            cpu_millis=cpu_millis, memory_mb=memory_mb, disk_free_mb=disk_free_mb,
            workspace_source=str(source_real), workspace_target="/workspace",
            workspace_root_id=approved_id, approved_workspace_root_id=approved_id,
            workspace_within_approved_root=True, workspace_symlink_free=True,
            workspace_writable=writable,
        )
