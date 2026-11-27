"""Private, non-authoritative snapshots for local worker diagnostics.

A snapshot describes the last observation; it is not a lease, liveness proof,
or permission to execute tasks. Consumers must treat expired/malformed data as
unknown and must never infer process identity from a PID alone.
"""
from __future__ import annotations

import json
import os
import stat
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


SCHEMA_VERSION = 1
MAX_SNAPSHOT_BYTES = 16 * 1024
_DEFAULT_MAX_AGE_SECONDS = 60


@dataclass(frozen=True)
class WorkerHealth:
    state: str
    observed_at: float | None
    age_seconds: float | None
    details: dict[str, Any] | None = None


def _private_parent(path: Path) -> None:
    parent = path.parent
    info = parent.lstat()
    if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
        raise ValueError("Worker health parent must be a real directory.")
    if os.name == "posix" and info.st_mode & 0o077:
        raise PermissionError("Worker health parent must be owner-private.")


def write_worker_health(
    path: str | Path,
    *,
    observed_at: float | None = None,
    details: Mapping[str, Any] | None = None,
) -> None:
    """Atomically replace one bounded private JSON snapshot."""
    target = Path(path)
    _private_parent(target)
    timestamp = time.time() if observed_at is None else observed_at
    if isinstance(timestamp, bool) or not isinstance(timestamp, (int, float)):
        raise ValueError("observed_at must be a finite timestamp.")
    if not (float("-inf") < float(timestamp) < float("inf")):
        raise ValueError("observed_at must be a finite timestamp.")
    if details is not None and not isinstance(details, Mapping):
        raise ValueError("details must be a mapping.")
    payload = {
        "schema_version": SCHEMA_VERSION,
        "observed_at": float(timestamp),
        "details": dict(details or {}),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    if len(encoded) > MAX_SNAPSHOT_BYTES:
        raise ValueError("Worker health snapshot exceeds the size limit.")
    fd, temporary = tempfile.mkstemp(prefix=".worker-health-", dir=str(target.parent))
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
        if os.name == "posix":
            directory_fd = os.open(target.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def read_worker_health(
    path: str | Path,
    *,
    now: float | None = None,
    max_age_seconds: float = _DEFAULT_MAX_AGE_SECONDS,
) -> WorkerHealth:
    """Read a snapshot; freshness is diagnostic and never proves liveness."""
    if isinstance(max_age_seconds, bool) or not isinstance(max_age_seconds, (int, float)) or max_age_seconds < 0:
        raise ValueError("max_age_seconds must be a non-negative number.")
    target = Path(path)
    try:
        _private_parent(target)
        info = target.lstat()
        if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
            return WorkerHealth("invalid", None, None)
        if os.name == "posix" and info.st_mode & 0o077:
            return WorkerHealth("invalid", None, None)
        if info.st_size > MAX_SNAPSHOT_BYTES:
            return WorkerHealth("invalid", None, None)
        with target.open("rb") as stream:
            raw = stream.read(MAX_SNAPSHOT_BYTES + 1)
    except FileNotFoundError:
        return WorkerHealth("missing", None, None)
    except (OSError, ValueError, PermissionError):
        return WorkerHealth("invalid", None, None)
    try:
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict) or payload.get("schema_version") != SCHEMA_VERSION:
            raise ValueError
        observed = payload.get("observed_at")
        details = payload.get("details")
        if isinstance(observed, bool) or not isinstance(observed, (int, float)):
            raise ValueError
        if not (float("-inf") < float(observed) < float("inf")) or not isinstance(details, dict):
            raise ValueError
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError, OverflowError):
        return WorkerHealth("invalid", None, None)
    current = time.time() if now is None else now
    if isinstance(current, bool) or not isinstance(current, (int, float)) or not (float("-inf") < float(current) < float("inf")):
        raise ValueError("now must be a finite timestamp.")
    age = max(0.0, float(current) - float(observed))
    state = "fresh" if age <= max_age_seconds else "stale"
    return WorkerHealth(state, float(observed), age, details)
