"""Read-only Linux LSM observations; labels are diagnostics, never attestation."""
from __future__ import annotations

import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path

from core.linux_host_evidence import _parse_start_time
from core.sandbox_runtime import SandboxRuntimeError

_MAX_TEXT = 65_536
_LSM_NAME = re.compile(r"^[A-Za-z0-9_-]+$")
_SELINUX_STATE = {"0": "permissive", "1": "enforcing"}


@dataclass(frozen=True)
class LinuxLsmSnapshot:
    pid: int
    start_time_ticks: int
    active_lsms: tuple[str, ...] | None
    process_security_context: str | None
    apparmor_profile: str | None
    apparmor_mode: str | None
    selinux_mode: str | None
    observed_sources: tuple[str, ...]
    unavailable_sources: tuple[str, ...]
    unknown_controls: tuple[str, ...]
    evidence_class: str = "diagnostic_only"
    attested: bool = False
    verified: bool = False


def _read_optional(path: Path, *, limit: int = _MAX_TEXT) -> str | None:
    descriptor = None
    try:
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(str(path), flags)
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise SandboxRuntimeError("LSM observation is not a regular file")
        data = bytearray()
        while len(data) <= limit:
            chunk = os.read(descriptor, min(8192, limit + 1 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
        if len(data) > limit:
            raise SandboxRuntimeError("LSM observation exceeds its size limit")
        try:
            return bytes(data).decode("ascii", errors="strict").strip()
        except UnicodeError as exc:
            raise SandboxRuntimeError("LSM observation is not valid ASCII") from exc
    except FileNotFoundError:
        return None
    except SandboxRuntimeError:
        raise
    except OSError as exc:
        raise SandboxRuntimeError("LSM observation is unavailable or unreadable") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _parse_active_lsms(payload: str | None) -> tuple[str, ...] | None:
    if payload is None:
        return None
    names = payload.split(",") if payload else []
    if not names or any(not _LSM_NAME.fullmatch(name) for name in names):
        raise SandboxRuntimeError("kernel LSM list is malformed")
    if len(set(names)) != len(names):
        raise SandboxRuntimeError("kernel LSM list contains duplicates")
    return tuple(names)


def _parse_apparmor_context(context: str | None) -> tuple[str | None, str | None]:
    if context is None:
        return None, None
    suffix = re.fullmatch(r"(.+?) \((enforce|complain|kill)\)", context)
    if suffix:
        return suffix.group(1), suffix.group(2)
    if not context or "\x00" in context or any(ord(char) < 32 for char in context):
        raise SandboxRuntimeError("process security context is malformed")
    return context, "unknown"


def collect_linux_lsm_snapshot(
    pid: int, *, proc_root: str = "/proc", sys_root: str = "/sys"
) -> LinuxLsmSnapshot:
    """Observe LSM list, process context, and SELinux mode with bounded reads."""
    if os.name != "posix":
        raise SandboxRuntimeError("Linux LSM observations require a POSIX host")
    if type(pid) is not int or pid <= 0:
        raise SandboxRuntimeError("process ID must be a positive integer")
    for root_value, label in ((proc_root, "procfs"), (sys_root, "sysfs")):
        if type(root_value) is not str or not root_value or not os.path.isabs(root_value):
            raise SandboxRuntimeError(label + " root must be an absolute path")
        try:
            info = Path(root_value).lstat()
        except OSError as exc:
            raise SandboxRuntimeError(label + " root is unavailable") from exc
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise SandboxRuntimeError(label + " root must be a non-symlink directory")

    process = Path(proc_root) / str(pid)
    try:
        info = process.lstat()
    except OSError as exc:
        raise SandboxRuntimeError("process entry is unavailable") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise SandboxRuntimeError("process entry must be a non-symlink directory")

    first_start = _parse_start_time(_read_required(process / "stat"))
    context = _read_optional(process / "attr" / "current")
    active = _parse_active_lsms(
        _read_optional(Path(sys_root) / "kernel" / "security" / "lsm")
    )
    selinux_raw = _read_optional(Path(sys_root) / "fs" / "selinux" / "enforce")
    if selinux_raw is not None and selinux_raw not in _SELINUX_STATE:
        raise SandboxRuntimeError("SELinux enforcement state is malformed")
    selinux_mode = _SELINUX_STATE.get(selinux_raw)
    second_start = _parse_start_time(_read_required(process / "stat"))
    if first_start != second_start:
        raise SandboxRuntimeError("process identity changed during LSM observation")

    apparmor_profile, apparmor_mode = _parse_apparmor_context(context)
    observed = ["procfs:stat"]
    unavailable = []
    if context is None:
        unavailable.append("procfs:attr/current")
    else:
        observed.append("procfs:attr/current")
    if active is None:
        unavailable.append("sysfs:kernel/security/lsm")
    else:
        observed.append("sysfs:kernel/security/lsm")
    if selinux_raw is None:
        unavailable.append("sysfs:fs/selinux/enforce")
    else:
        observed.append("sysfs:fs/selinux/enforce")

    return LinuxLsmSnapshot(
        pid=pid,
        start_time_ticks=first_start,
        active_lsms=active,
        process_security_context=context,
        apparmor_profile=apparmor_profile if active and "apparmor" in active else None,
        apparmor_mode=apparmor_mode if active and "apparmor" in active else None,
        selinux_mode=selinux_mode if active and "selinux" in active else None,
        observed_sources=tuple(observed),
        unavailable_sources=tuple(unavailable),
        unknown_controls=(
            "procfs and sysfs roots are caller supplied and not independently attested",
            "active LSM list does not prove a policy is loaded or enforced for this workload",
            "AppArmor profile label does not prove policy completeness or deny behavior",
            "SELinux enforcing flag is global and does not prove process-specific policy outcomes",
            "LSM stacking order, policy contents, transitions, and audit events are not inspected",
            "process security context may change after observation",
            "mount, network, seccomp, cgroup, namespace, device, socket, and secret isolation remain unverified",
        ),
        evidence_class="diagnostic_only",
        attested=False,
        verified=False,
    )


def _read_required(path: Path) -> str:
    value = _read_optional(path)
    if value is None:
        raise SandboxRuntimeError("required process identity record is unavailable")
    return value
