"""Bounded Linux mountinfo observations for diagnostics, never attestation."""
from __future__ import annotations

import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path

from core.sandbox_runtime import SandboxRuntimeError

_MAX_MOUNTINFO_BYTES = 1_048_576
_MAX_MOUNTS = 4096
_UINT_RE = re.compile(r"^[0-9]+$")
_DEVICE_RE = re.compile(r"^[0-9]+:[0-9]+$")
_ESCAPE_RE = re.compile(r"\\([0-7]{3})")


@dataclass(frozen=True)
class LinuxMountEntry:
    mount_id: int
    parent_id: int
    device: str
    root: str
    mount_point: str
    mount_options: tuple[str, ...]
    optional_fields: tuple[str, ...]
    filesystem_type: str
    source: str
    super_options: tuple[str, ...]
    mount_read_only: bool
    superblock_read_only: bool


@dataclass(frozen=True)
class LinuxMountSnapshot:
    pid: int
    entries: tuple[LinuxMountEntry, ...]
    observed_source: str
    unknown_controls: tuple[str, ...]
    attested: bool = False


def _decode_mount_field(value: str) -> str:
    # Reject unescaped backslashes before decoding valid procfs octal escapes.
    if re.search(r"\\\\(?![0-7]{3})", value):
        raise SandboxRuntimeError("mountinfo contains an invalid escape sequence")
    return _ESCAPE_RE.sub(lambda match: chr(int(match.group(1), 8)), value)

def _options(value: str, label: str) -> tuple[str, ...]:
    items = tuple(value.split(","))
    if not items or any(not item or any(ch.isspace() for ch in item) for item in items):
        raise SandboxRuntimeError("mountinfo " + label + " are malformed")
    if len(set(items)) != len(items):
        raise SandboxRuntimeError("mountinfo " + label + " contain duplicates")
    return items


def parse_mountinfo(payload: str, *, pid: int) -> LinuxMountSnapshot:
    """Parse one bounded /proc/<pid>/mountinfo payload."""
    if type(pid) is not int or pid <= 0 or type(payload) is not str:
        raise SandboxRuntimeError("mountinfo input is invalid")
    encoded = payload.encode("utf-8", errors="strict")
    if len(encoded) > _MAX_MOUNTINFO_BYTES:
        raise SandboxRuntimeError("mountinfo exceeds its size limit")
    lines = payload.splitlines()
    if not lines or len(lines) > _MAX_MOUNTS:
        raise SandboxRuntimeError("mountinfo has an invalid number of entries")
    entries: list[LinuxMountEntry] = []
    seen_ids: set[int] = set()
    for line in lines:
        fields = line.split()
        try:
            separator = fields.index("-")
        except ValueError as exc:
            raise SandboxRuntimeError("mountinfo separator is missing") from exc
        if separator < 6 or len(fields) != separator + 4:
            raise SandboxRuntimeError("mountinfo entry has an invalid field count")
        mount_id, parent_id = fields[0], fields[1]
        if not _UINT_RE.fullmatch(mount_id) or not _UINT_RE.fullmatch(parent_id):
            raise SandboxRuntimeError("mountinfo identifiers are malformed")
        mount_id_int, parent_id_int = int(mount_id), int(parent_id)
        if mount_id_int <= 0 or parent_id_int < 0 or mount_id_int in seen_ids:
            raise SandboxRuntimeError("mountinfo identifiers are invalid or duplicated")
        seen_ids.add(mount_id_int)
        device = fields[2]
        if not _DEVICE_RE.fullmatch(device):
            raise SandboxRuntimeError("mountinfo device identifier is malformed")
        root = _decode_mount_field(fields[3])
        mount_point = _decode_mount_field(fields[4])
        if not root.startswith("/") or not mount_point.startswith("/"):
            raise SandboxRuntimeError("mountinfo paths must be absolute")
        mount_opts = _options(fields[5], "mount options")
        optional = tuple(fields[6:separator])
        if any(not item or any(ch.isspace() for ch in item) for item in optional):
            raise SandboxRuntimeError("mountinfo optional fields are malformed")
        fs_type, source, super_value = fields[separator + 1:]
        if not fs_type or not source:
            raise SandboxRuntimeError("mountinfo filesystem fields are malformed")
        super_opts = _options(super_value, "superblock options")
        entries.append(LinuxMountEntry(
            mount_id=mount_id_int,
            parent_id=parent_id_int,
            device=device,
            root=root,
            mount_point=mount_point,
            mount_options=mount_opts,
            optional_fields=optional,
            filesystem_type=fs_type,
            source=_decode_mount_field(source),
            super_options=super_opts,
            mount_read_only="ro" in mount_opts,
            superblock_read_only="ro" in super_opts,
        ))
    return LinuxMountSnapshot(
        pid=pid,
        entries=tuple(entries),
        observed_source="procfs:mountinfo",
        unknown_controls=(
            "mountinfo is read from a caller-selected procfs root and is not host-attested",
            "mount options are observations and do not prove effective write denial",
            "mount namespace identity is not compared with a trusted host baseline",
            "bind-mount propagation and parent relationships are not policy-validated",
            "mounts may change immediately after observation",
            "device, socket, and host-secret exposure require separate checks",
        ),
        attested=False,
    )


def collect_linux_mount_snapshot(pid: int, *, proc_root: str = "/proc") -> LinuxMountSnapshot:
    """Read /proc/<pid>/mountinfo with no-follow and strict size bounds."""
    if os.name != "posix":
        raise SandboxRuntimeError("Linux mount observations require a POSIX host")
    if type(pid) is not int or pid <= 0:
        raise SandboxRuntimeError("process ID must be a positive integer")
    if type(proc_root) is not str or not proc_root or not os.path.isabs(proc_root):
        raise SandboxRuntimeError("procfs root must be an absolute path")
    root = Path(proc_root)
    try:
        root_info = root.lstat()
        process_info = (root / str(pid)).lstat()
    except OSError as exc:
        raise SandboxRuntimeError("procfs process entry is unavailable") from exc
    if stat.S_ISLNK(root_info.st_mode) or not stat.S_ISDIR(root_info.st_mode):
        raise SandboxRuntimeError("procfs root must be a non-symlink directory")
    if stat.S_ISLNK(process_info.st_mode) or not stat.S_ISDIR(process_info.st_mode):
        raise SandboxRuntimeError("procfs process entry must be a non-symlink directory")
    path = root / str(pid) / "mountinfo"
    descriptor = None
    try:
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(str(path), flags)
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise SandboxRuntimeError("mountinfo observation is not a regular file")
        chunks = bytearray()
        while len(chunks) <= _MAX_MOUNTINFO_BYTES:
            chunk = os.read(descriptor, min(8192, _MAX_MOUNTINFO_BYTES + 1 - len(chunks)))
            if not chunk:
                break
            chunks.extend(chunk)
    except SandboxRuntimeError:
        raise
    except OSError as exc:
        raise SandboxRuntimeError("mountinfo observation is unavailable") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
    if len(chunks) > _MAX_MOUNTINFO_BYTES:
        raise SandboxRuntimeError("mountinfo exceeds its size limit")
    try:
        payload = bytes(chunks).decode("ascii", errors="strict")
    except UnicodeError as exc:
        raise SandboxRuntimeError("mountinfo is not valid ASCII") from exc
    return parse_mountinfo(payload, pid=pid)
