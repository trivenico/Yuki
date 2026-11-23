"""Fail-closed host preflight for operator-composed gate-first deployments.

The preflight proves local executable/procfs/socket properties and requires
operator-owned verifiers for daemon user-namespace mapping and provenance
publisher ownership. It does not treat Docker inspect as host attestation.
"""
from __future__ import annotations

import hashlib
import os
import re
import stat
import sys
from pathlib import Path
from typing import Callable


class GateFirstHostPreflightError(RuntimeError):
    """The host cannot safely support the configured gate-first deployment."""


def _require_verifier(name: str, verifier: Callable[[], object]) -> None:
    if not callable(verifier):
        raise TypeError(f"{name} must be a trusted callable")
    try:
        result = verifier()
    except Exception as exc:
        raise GateFirstHostPreflightError(f"{name} failed") from exc
    if result is not True:
        raise GateFirstHostPreflightError(f"{name} did not positively verify its deployment invariant")


def _proc_mount_options(mountinfo: str) -> tuple[str, ...]:
    for line in mountinfo.splitlines():
        fields = line.split()
        if len(fields) < 10 or fields[4] != "/proc" or "-" not in fields:
            continue
        separator = fields.index("-")
        if separator + 3 >= len(fields) or fields[separator + 1] != "proc":
            raise GateFirstHostPreflightError("/proc is not backed by procfs")
        return tuple(fields[5].split(",") + fields[separator + 3].split(","))
    raise GateFirstHostPreflightError("host procfs mount cannot be identified")


def validate_gate_first_host_environment(
    *,
    docker_binary: str,
    expected_docker_sha256: str,
    docker_socket_path: str,
    private_directory_root: str,
    expected_host_uid: int,
    uid_resolver: Callable[[int], int],
    exec_observer: Callable[..., bool],
    supervisor_collector_factory: Callable[..., object],
    trusted_evidence_builder: Callable[..., object],
    user_namespace_mapping_verifier: Callable[[], object],
    release_publisher_ownership_verifier: Callable[[], object],
    proc_root: str = "/proc",
    platform: str | None = None,
) -> dict[str, object]:
    """Validate deployment prerequisites before constructing gate sessions.

    The two verifier callbacks must inspect the actual Docker daemon/container
    UID mapping policy and the immutable release publisher/store/key ownership.
    They are deliberately mandatory: those properties cannot be inferred from
    the worker process namespace or Docker CLI executable alone.
    """
    current_platform = sys.platform if platform is None else platform
    if current_platform != "linux":
        raise GateFirstHostPreflightError("gate-first Docker deployment requires Linux host procfs semantics")
    if type(expected_docker_sha256) is not str or not re.fullmatch(r"[0-9a-f]{64}", expected_docker_sha256):
        raise ValueError("expected_docker_sha256 must be a lowercase SHA-256 digest")
    if type(expected_host_uid) is not int or expected_host_uid <= 0 or expected_host_uid != os.geteuid():
        raise GateFirstHostPreflightError("expected_host_uid must equal the non-root worker service effective UID")
    if not callable(uid_resolver) or not callable(exec_observer):
        raise TypeError("trusted Linux gate identity resolver and exec observer are required")
    if not callable(supervisor_collector_factory) or not callable(trusted_evidence_builder):
        raise TypeError("trusted supervisor collector factory and host evidence builder are required")

    executable = Path(docker_binary)
    if not executable.is_absolute() or executable.is_symlink():
        raise GateFirstHostPreflightError("Docker executable must be an absolute non-symlink path")
    try:
        executable_stat = executable.stat(follow_symlinks=False)
        if not stat.S_ISREG(executable_stat.st_mode) or not os.access(executable, os.X_OK):
            raise GateFirstHostPreflightError("Docker executable is not a regular executable file")
        if executable_stat.st_uid not in {0, os.geteuid()} or executable_stat.st_mode & 0o022:
            raise GateFirstHostPreflightError("Docker executable ownership or write permissions are unsafe")
        digest = hashlib.sha256()
        with executable.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        if digest.hexdigest() != expected_docker_sha256:
            raise GateFirstHostPreflightError("Docker executable digest differs from operator-pinned identity")
    except OSError as exc:
        raise GateFirstHostPreflightError("Docker executable could not be inspected safely") from exc

    socket_path = Path(docker_socket_path)
    if not socket_path.is_absolute() or socket_path.is_symlink():
        raise GateFirstHostPreflightError("Docker Engine endpoint must be an absolute non-symlink Unix socket")
    try:
        socket_stat = socket_path.stat(follow_symlinks=False)
    except OSError as exc:
        raise GateFirstHostPreflightError("Docker Engine Unix socket is unavailable") from exc
    if (not stat.S_ISSOCK(socket_stat.st_mode) or socket_stat.st_uid != os.geteuid()
            or socket_stat.st_mode & 0o022):
        raise GateFirstHostPreflightError("Docker Engine socket ownership or permissions are unsafe")

    root = Path(private_directory_root)
    if not root.is_absolute() or root.is_symlink() or not root.is_dir():
        raise GateFirstHostPreflightError("gate private-directory root must exist and be a non-symlink directory")
    root_stat = root.stat(follow_symlinks=False)
    if root_stat.st_uid != os.geteuid() or root_stat.st_mode & 0o077:
        raise GateFirstHostPreflightError("gate private-directory root must be service-owned and mode 0700 or stricter")

    proc = Path(proc_root)
    try:
        mountinfo = (proc / "self/mountinfo").read_text(encoding="ascii")
        options = _proc_mount_options(mountinfo)
        status = (proc / "self/status").read_text(encoding="ascii")
        cmdline = (proc / "self/cmdline").read_bytes()
        uid_map = (proc / "self/uid_map").read_text(encoding="ascii")
    except (OSError, UnicodeError) as exc:
        raise GateFirstHostPreflightError("host procfs visibility is incomplete") from exc
    if any(option.startswith("hidepid=") and option not in {"hidepid=0"} for option in options):
        raise GateFirstHostPreflightError("procfs hidepid policy may conceal the Docker gate process")
    if not cmdline or b"\x00" not in cmdline or "Uid:" not in status:
        raise GateFirstHostPreflightError("host procfs cmdline or UID visibility is insufficient")
    if not uid_map.strip():
        raise GateFirstHostPreflightError("host user-namespace mapping is unavailable")

    _require_verifier("Docker gate user-namespace mapping verifier", user_namespace_mapping_verifier)
    _require_verifier("release publisher ownership verifier", release_publisher_ownership_verifier)

    return {
        "platform": "linux",
        "docker_binary": str(executable.resolve(strict=True)),
        "docker_sha256": digest.hexdigest(),
        "docker_socket": str(socket_path.resolve(strict=True)),
        "service_uid": os.geteuid(),
        "procfs_options": options,
        "uid_map": uid_map.strip(),
        "user_namespace_mapping_verified": True,
        "release_publisher_ownership_verified": True,
        "trusted_identity_callbacks": True,
        "trusted_supervisor_collector": True,
        "trusted_evidence_builder": True,
    }
