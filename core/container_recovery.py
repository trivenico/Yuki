"""Conservative Docker container reconciliation for interrupted Yuki jobs.

Only containers with both Yuki ownership and the exact execution ID labels are
eligible for cleanup. This module never starts or replays a proposal execution.
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from typing import Callable, Sequence


class ContainerRecoveryError(RuntimeError):
    """Docker state could not be safely reconciled."""


_EXECUTION_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_OWNER_LABEL = "com.yuki.managed"
_EXECUTION_LABEL = "com.yuki.execution-id"


@dataclass(frozen=True)
class ContainerRecoveryResult:
    execution_id: str
    inspected: bool
    removed: bool
    already_absent: bool


def container_name_for_execution(execution_id: str) -> str:
    if not isinstance(execution_id, str) or not _EXECUTION_ID_RE.fullmatch(execution_id):
        raise ContainerRecoveryError("invalid execution ID for container identity")
    # Docker names are deterministic and bounded; label values carry the full ID.
    import hashlib
    suffix = hashlib.sha256(execution_id.encode("utf-8")).hexdigest()[:16]
    return "yuki-job-" + suffix


def _docker(args: Sequence[str], *, timeout_seconds: int) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            ["docker", *args],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            close_fds=True,
            timeout=timeout_seconds,
            check=False,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ContainerRecoveryError("Docker state could not be queried safely.") from exc


def reconcile_execution_container(execution_id: str, *, before_remove: Callable[[], bool] | None = None) -> ContainerRecoveryResult:
    """Remove a stopped/running container only after exact label verification."""
    name = container_name_for_execution(execution_id)
    inspected = _docker(
        ["inspect", "--format", "{{.Id}}\t{{.Name}}\t{{index .Config.Labels \"com.yuki.managed\"}}\t{{index .Config.Labels \"com.yuki.execution-id\"}}", name],
        timeout_seconds=5,
    )
    if inspected.returncode != 0:
        # A missing container is safe only when Docker explicitly reports no such object.
        message = (inspected.stderr or "").lower()
        if "no such object" in message or "no such container" in message:
            return ContainerRecoveryResult(execution_id, inspected=False, removed=False, already_absent=True)
        raise ContainerRecoveryError("Docker inspect failed; ownership cannot be established.")

    parts = inspected.stdout.rstrip("\\r\\n").split("\\t")
    if len(parts) != 4:
        raise ContainerRecoveryError("Docker inspect returned malformed ownership metadata.")
    container_id, actual_name, managed, actual_execution_id = parts
    if not re.fullmatch(r"[0-9a-f]{64}", container_id):
        raise ContainerRecoveryError("Docker inspect returned an invalid immutable container ID.")
    if actual_name.lstrip("/") != name or managed != "true" or actual_execution_id != execution_id:
        raise ContainerRecoveryError("Container ownership labels do not match; refusing cleanup.")

    if before_remove is not None:
        try:
            authorized = before_remove()
        except Exception as exc:
            raise ContainerRecoveryError("Recovery claim could not be verified before container removal.") from exc
        if authorized is not True:
            raise ContainerRecoveryError("Recovery claim expired or changed before container removal.")
    # Remove by immutable ID so a container recreated under the same name cannot be targeted.\n    removed = _docker(["rm", "-f", container_id], timeout_seconds=10)
    if removed.returncode != 0:
        raise ContainerRecoveryError("Verified Yuki container cleanup failed.")
    return ContainerRecoveryResult(execution_id, inspected=True, removed=True, already_absent=False)
