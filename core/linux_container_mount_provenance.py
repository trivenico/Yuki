"""Validate application-root provenance against a container mount namespace.

The caller must obtain mountinfo from the host PID already bound to the exact
container identity. This module never trusts Docker Mounts alone: it validates
the process mount namespace and rejects any application root overlapped by a
mount, including task-controlled workspace mounts.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath

from core.host_runtime_inventory import RuntimeInventoryError

_MAX_MOUNTINFO_BYTES = 1024 * 1024
_MAX_MOUNTS = 4096
_OCTAL_ESCAPE = re.compile(r"\\([0-7]{3})")


def _decode_mount_field(value: str) -> str:
    def replace(match: re.Match[str]) -> str:
        code = int(match.group(1), 8)
        if code == 0 or code == 0x5C:
            raise RuntimeInventoryError("container mountinfo contains a forbidden escaped path byte.")
        return chr(code)
    decoded = _OCTAL_ESCAPE.sub(replace, value)
    if "\\" in decoded or "\x00" in decoded:
        raise RuntimeInventoryError("container mountinfo path encoding is ambiguous.")
    return decoded


def _canonical_absolute_path(value: str, label: str) -> str:
    if (type(value) is not str or not value.startswith("/") or len(value) > 4096
            or "\x00" in value or "\\" in value):
        raise RuntimeInventoryError(f"{label} must be a bounded canonical absolute path.")
    path = PurePosixPath(value)
    if str(path) != value.rstrip("/") and value != "/":
        raise RuntimeInventoryError(f"{label} is not canonical.")
    if any(part in {".", ".."} for part in value.split("/")):
        raise RuntimeInventoryError(f"{label} contains traversal components.")
    return "/" if value == "/" else value.rstrip("/")


@dataclass(frozen=True, slots=True)
class ContainerMount:
    mount_id: int
    parent_id: int
    root: str
    mountpoint: str
    filesystem: str
    source: str


def parse_container_mountinfo(raw: str | bytes) -> tuple[ContainerMount, ...]:
    """Parse Linux /proc/<pid>/mountinfo with strict bounds and escaping."""
    if type(raw) is bytes:
        if len(raw) > _MAX_MOUNTINFO_BYTES:
            raise RuntimeInventoryError("container mountinfo exceeds its byte ceiling.")
        try:
            raw = raw.decode("ascii", errors="strict")
        except UnicodeError as exc:
            raise RuntimeInventoryError("container mountinfo is not ASCII.") from exc
    elif type(raw) is not str or len(raw.encode("utf-8")) > _MAX_MOUNTINFO_BYTES:
        raise RuntimeInventoryError("container mountinfo input is invalid or oversized.")
    mounts: list[ContainerMount] = []
    ids: set[int] = set()
    for line in raw.splitlines():
        if not line or len(line) > 8192:
            raise RuntimeInventoryError("container mountinfo contains an empty or oversized record.")
        fields = line.split()
        try:
            separator = fields.index("-")
        except ValueError as exc:
            raise RuntimeInventoryError("container mountinfo record lacks its separator.") from exc
        if separator < 6 or len(fields) < separator + 4:
            raise RuntimeInventoryError("container mountinfo record is incomplete.")
        try:
            mount_id, parent_id = int(fields[0]), int(fields[1])
        except ValueError as exc:
            raise RuntimeInventoryError("container mountinfo identifiers are invalid.") from exc
        if mount_id <= 0 or parent_id < 0 or mount_id in ids:
            raise RuntimeInventoryError("container mountinfo identifiers are duplicate or invalid.")
        ids.add(mount_id)
        root = _canonical_absolute_path(_decode_mount_field(fields[3]), "mount root")
        mountpoint = _canonical_absolute_path(_decode_mount_field(fields[4]), "mountpoint")
        filesystem = fields[separator + 1]
        source = _decode_mount_field(fields[separator + 2])
        if not re.fullmatch(r"[A-Za-z0-9_.+-]{1,64}", filesystem) or len(source) > 1024:
            raise RuntimeInventoryError("container mountinfo filesystem metadata is invalid.")
        mounts.append(ContainerMount(mount_id, parent_id, root, mountpoint, filesystem, source))
        if len(mounts) > _MAX_MOUNTS:
            raise RuntimeInventoryError("container mount count exceeds its ceiling.")
    if not mounts or not any(item.mountpoint == "/" for item in mounts):
        raise RuntimeInventoryError("container mountinfo does not describe a root mount.")
    return tuple(mounts)


def _overlaps(left: str, right: str) -> bool:
    return left == right or left == "/" or right.startswith(left + "/") or left.startswith(right + "/")


def verify_image_owned_application_root(
    application_root: str,
    mounts: tuple[ContainerMount, ...],
    *,
    excluded_task_mounts: tuple[str, ...] = ("/workspace",),
) -> str:
    """Return canonical root only when no mount overlaps the app tree.

    Requiring zero mount overlap is intentionally conservative: even a nested
    mount below an otherwise image-owned app root can replace a dependency
    directory or package-lock path with task-controlled content.
    """
    root = _canonical_absolute_path(application_root, "application root")
    if root == "/":
        raise RuntimeInventoryError("container root cannot be treated as an application root.")
    if not isinstance(mounts, tuple) or not mounts:
        raise RuntimeInventoryError("verified container mount table is required.")
    excluded = tuple(
        _canonical_absolute_path(path, "excluded task mount")
        for path in excluded_task_mounts
    )
    if not excluded or "/workspace" not in excluded:
        raise RuntimeInventoryError("the task-controlled /workspace mount must be explicitly excluded.")
    root_mounts = [item for item in mounts if item.mountpoint == "/"]
    if len(root_mounts) != 1:
        raise RuntimeInventoryError("container mount table must contain exactly one root mount.")
    if sum(item.mountpoint == "/workspace" for item in mounts) != 1:
        raise RuntimeInventoryError("container mount table must expose exactly one task workspace mount.")
    for mount in mounts:
        if mount.mountpoint == "/":
            continue
        if _overlaps(root, mount.mountpoint):
            raise RuntimeInventoryError("application root overlaps a container mount and is not image-owned.")
        if any(_overlaps(root, task_mount) for task_mount in excluded):
            raise RuntimeInventoryError("application root overlaps an excluded task-controlled mount.")
    return root
