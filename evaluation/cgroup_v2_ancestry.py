"""Read-only verification of cgroup v2 limits along a runner's ancestry.

This module does not create cgroups, move tasks, change controller state, or
launch a process. It verifies observed control-file ceilings only; it cannot
reserve capacity or prove that a process remains in the inspected cgroup.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from evaluation.cgroup_v2_preflight import (
    _read_control_file,
    verify_parent_resource_ceiling,
)
from evaluation.cgroup_v2_fd import (
    PinnedCgroupDirectory,
    open_pinned_cgroup_directory,
    pin_cgroup_mount_root,
    read_pinned_cgroup_control,
)
from evaluation.runner_isolation_policy import (
    RunnerIsolationPolicy,
    verify_cgroup_v2_readback,
)

_CONTROL_FILES = ("cpu.max", "memory.max", "memory.swap.max", "pids.max")
_MAX_DEPTH = 64


@dataclass(frozen=True, slots=True)
class CgroupV2AncestryCheck:
    verified: bool
    mount_root: str
    child_path: str
    inspected_ancestors: tuple[str, ...]
    failed_ancestors: tuple[str, ...]
    reason: str


def _read_limits(directory: Path) -> dict[str, str]:
    return {
        name: _read_control_file(directory / name, limit=256)
        for name in _CONTROL_FILES
    }


def verify_cgroup_v2_ancestry(
    policy: RunnerIsolationPolicy,
    *,
    mount_root: str | Path,
    child_path: str | Path,
) -> CgroupV2AncestryCheck:
    """Verify child read-back and every ancestor ceiling up to mount_root.

    Both paths must already exist on the same cgroup v2 filesystem. The
    caller must supply the actual cgroup mount root and the dedicated child
    cgroup directory; path resolution and containment are checked fail-closed.
    """
    root_label = str(mount_root)[:512]
    child_label = str(child_path)[:512]
    inspected: list[str] = []
    failed: list[str] = []
    try:
        if not isinstance(policy, RunnerIsolationPolicy) or not policy.verify():
            raise ValueError("invalid runner policy")
        root = Path(mount_root).resolve(strict=True)
        child = Path(child_path).resolve(strict=True)
        if not root.is_dir() or not child.is_dir():
            raise ValueError("cgroup paths must be directories")
        relative = child.relative_to(root)
        if not relative.parts or len(relative.parts) > _MAX_DEPTH:
            raise ValueError("child path is not a bounded descendant of mount root")

        child_limits = _read_limits(child)
        if not verify_cgroup_v2_readback(policy, child_limits):
            return CgroupV2AncestryCheck(
                False, str(root), str(child), (), (str(child),),
                "child_limits_mismatch",
            )

        current = child.parent
        while True:
            try:
                limits = _read_limits(current)
                if not verify_parent_resource_ceiling(policy, limits):
                    failed.append(str(current))
            except (OSError, UnicodeError, ValueError, TypeError):
                failed.append(str(current))
            inspected.append(str(current))
            if len(inspected) > _MAX_DEPTH:
                raise ValueError("cgroup ancestry exceeds depth limit")
            if current == root:
                break
            parent = current.parent
            if parent == current or not parent.is_relative_to(root):
                raise ValueError("cgroup ancestry escaped mount root")
            current = parent

        if failed:
            return CgroupV2AncestryCheck(
                False, str(root), str(child), tuple(inspected), tuple(failed),
                "ancestor_limit_mismatch_or_unreadable",
            )
        return CgroupV2AncestryCheck(
            True, str(root), str(child), tuple(inspected), (),
            "configured_ceilings_verified",
        )
    except (OSError, ValueError, TypeError, RuntimeError, RecursionError):
        return CgroupV2AncestryCheck(
            False, root_label, child_label, tuple(inspected), tuple(failed),
            "unavailable_or_malformed",
        )


def _read_pinned_limits(directory: PinnedCgroupDirectory) -> dict[str, str]:
    return {
        name: read_pinned_cgroup_control(directory, name)
        for name in _CONTROL_FILES
    }


def verify_cgroup_v2_ancestry_pinned(
    policy: RunnerIsolationPolicy,
    *,
    trusted_mount_root_fd: int,
    child_relative_path: str,
    mount_root_label: str = "<pinned-cgroup-mount>",
) -> CgroupV2AncestryCheck:
    """Verify child and ancestor ceilings using only pinned directory FDs.

    The caller must provide an already trusted descriptor for the actual
    cgroup v2 mount root. The relative path is walked with O_NOFOLLOW and
    mount-identity checks; no path resolution is used for trust decisions.
    This is a point-in-time read-only check, not a capacity reservation or
    proof that a process remains in the inspected cgroup.
    """
    root_label = str(mount_root_label)[:512]
    child_label = root_label.rstrip("/") + "/" + str(child_relative_path)[:1024]
    inspected: list[str] = []
    failed: list[str] = []
    root_directory: PinnedCgroupDirectory | None = None
    try:
        if not isinstance(policy, RunnerIsolationPolicy) or not policy.verify():
            raise ValueError("invalid runner policy")
        if type(child_relative_path) is not str:
            raise ValueError("child relative path must be text")
        parts = child_relative_path.split("/")
        if not parts or len(parts) > _MAX_DEPTH or any(
            not part or part in (".", "..") for part in parts
        ):
            raise ValueError("child relative path is not canonical or bounded")
        root_directory = pin_cgroup_mount_root(trusted_mount_root_fd)
        child_directory = open_pinned_cgroup_directory(
            root_directory.fileno(), child_relative_path
        )
        try:
            child_limits = _read_pinned_limits(child_directory)
            if not verify_cgroup_v2_readback(policy, child_limits):
                return CgroupV2AncestryCheck(
                    False, root_label, child_label, (), (child_label,),
                    "child_limits_mismatch",
                )
        finally:
            child_directory.close()

        for depth in range(len(parts) - 1, -1, -1):
            relative = "/".join(parts[:depth])
            label = root_label.rstrip("/") + ("/" + relative if relative else "")
            directory = (
                pin_cgroup_mount_root(trusted_mount_root_fd)
                if not relative
                else open_pinned_cgroup_directory(
                    root_directory.fileno(), relative
                )
            )
            try:
                limits = _read_pinned_limits(directory)
                if not verify_parent_resource_ceiling(policy, limits):
                    failed.append(label)
            except (OSError, UnicodeError, ValueError, TypeError):
                failed.append(label)
            finally:
                directory.close()
            inspected.append(label)

        if failed:
            return CgroupV2AncestryCheck(
                False, root_label, child_label, tuple(inspected), tuple(failed),
                "ancestor_limit_mismatch_or_unreadable",
            )
        return CgroupV2AncestryCheck(
            True, root_label, child_label, tuple(inspected), (),
            "configured_ceilings_verified",
        )
    except (OSError, UnicodeError, ValueError, TypeError, RuntimeError, RecursionError):
        return CgroupV2AncestryCheck(
            False, root_label, child_label, tuple(inspected), tuple(failed),
            "unavailable_or_malformed",
        )
    finally:
        if root_directory is not None:
            root_directory.close()
