"""Durable, fail-closed monotonic release state for portable Yuki bundles.

This module stores only signed release sequence numbers and manifest digests.
The host installer must place the state directory outside removable media and
protect it with host ownership/ACLs.
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
from pathlib import Path
from typing import Any

from yuki_trust.portable_bootstrap import BootstrapError

_STATE_SCHEMA = 1
_MAX_STATE_BYTES = 16 * 1024


def _validate_state(value: Any) -> tuple[int, str]:
    if not isinstance(value, dict) or set(value) != {
        "schema_version", "highest_sequence", "manifest_sha256"
    }:
        raise BootstrapError("Anti-rollback state has an invalid schema.")
    if type(value["schema_version"]) is not int or value["schema_version"] != _STATE_SCHEMA:
        raise BootstrapError("Anti-rollback state schema version is unsupported.")
    sequence = value["highest_sequence"]
    digest = value["manifest_sha256"]
    if type(sequence) is not int or sequence < 0:
        raise BootstrapError("Anti-rollback state sequence is invalid.")
    if (
        not isinstance(digest, str) or len(digest) != 64
        or any(char not in "0123456789abcdef" for char in digest)
    ):
        raise BootstrapError("Anti-rollback state manifest digest is invalid.")
    return sequence, digest


def _read_state(path: Path) -> tuple[int, str] | None:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise BootstrapError("Anti-rollback state cannot be opened safely.") from exc
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise BootstrapError("Anti-rollback state must be a regular file.")
        if os.name != "nt":
            if info.st_uid not in {0, os.getuid()} or stat.S_IMODE(info.st_mode) & 0o077:
                raise BootstrapError("Anti-rollback state ownership or permissions are unsafe.")
        if info.st_size > _MAX_STATE_BYTES:
            raise BootstrapError("Anti-rollback state exceeds its size limit.")
        chunks: list[bytes] = []
        remaining = _MAX_STATE_BYTES + 1
        while remaining:
            chunk = os.read(fd, min(4096, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        if len(payload) > _MAX_STATE_BYTES:
            raise BootstrapError("Anti-rollback state exceeds its size limit.")
        try:
            value = json.loads(payload.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise BootstrapError("Anti-rollback state is corrupt; refusing to reset it.") from exc
        return _validate_state(value)
    finally:
        os.close(fd)


def _lock_state(path: Path):
    """Return an open, exclusively locked sidecar file handle."""
    lock_path = path.with_name(path.name + ".lock")
    flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(lock_path, flags, 0o600)
    except OSError as exc:
        raise BootstrapError("Anti-rollback lock cannot be opened safely.") from exc
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode):
        os.close(fd)
        raise BootstrapError("Anti-rollback lock must be a regular file.")
    if os.name != "nt" and (
        info.st_uid not in {0, os.getuid()} or stat.S_IMODE(info.st_mode) & 0o077
    ):
        os.close(fd)
        raise BootstrapError("Anti-rollback lock ownership or permissions are unsafe.")
    if os.name == "nt":
        import msvcrt
        if os.fstat(fd).st_size == 0:
            os.write(fd, b"0")
            os.fsync(fd)
        os.lseek(fd, 0, os.SEEK_SET)
        try:
            msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
        except OSError as exc:
            os.close(fd)
            raise BootstrapError("Unable to lock anti-rollback state.") from exc
    else:
        import fcntl
        fcntl.flock(fd, fcntl.LOCK_EX)
    return fd


def _unlock_state(fd: int) -> None:
    try:
        if os.name == "nt":
            import msvcrt
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def _atomic_write(path: Path, sequence: int, digest: str) -> None:
    payload = (
        json.dumps(
            {
                "schema_version": _STATE_SCHEMA,
                "highest_sequence": sequence,
                "manifest_sha256": digest,
            },
            sort_keys=True,
            separators=(",", ":"),
        ) + "\n"
    ).encode("utf-8")
    fd, temporary = tempfile.mkstemp(prefix=".yuki-rollback-", dir=path.parent)
    temp_path = Path(temporary)
    try:
        if os.name != "nt":
            os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_path, path)
        if os.name != "nt":
            directory_fd = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    except Exception:
        try:
            temp_path.unlink()
        except OSError:
            pass
        raise


def advance_release_state(
    state_path: str | Path,
    release_sequence: int,
    manifest_bytes: bytes,
) -> dict[str, int | str]:
    """Atomically accept a newer signed release, rejecting rollback/equivocation."""
    if type(release_sequence) is not int or release_sequence < 0:
        raise BootstrapError("Signed release sequence must be a non-negative integer.")
    if not isinstance(manifest_bytes, bytes) or not manifest_bytes:
        raise BootstrapError("Signed release manifest bytes are required.")
    path = Path(state_path).expanduser()
    if not path.is_absolute() or not path.parent.is_dir() or path.parent.is_symlink():
        raise BootstrapError("Anti-rollback state path must use an existing host-owned directory.")
    if path.is_symlink():
        raise BootstrapError("Anti-rollback state file must not be a symlink.")
    digest = hashlib.sha256(manifest_bytes).hexdigest()
    lock_fd = _lock_state(path)
    try:
        current = _read_state(path)
        if current is not None:
            highest, previous_digest = current
            if release_sequence < highest:
                raise BootstrapError(
                    f"Release rollback rejected: sequence {release_sequence} is older than accepted sequence {highest}."
                )
            if release_sequence == highest and digest != previous_digest:
                raise BootstrapError(
                    "Release sequence equivocation rejected: this sequence was already bound to another manifest."
                )
            if release_sequence == highest:
                return {"highest_sequence": highest, "manifest_sha256": digest}
        _atomic_write(path, release_sequence, digest)
        return {"highest_sequence": release_sequence, "manifest_sha256": digest}
    except BootstrapError:
        raise
    except OSError as exc:
        raise BootstrapError("Anti-rollback state could not be committed safely.") from exc
    finally:
        _unlock_state(lock_fd)
