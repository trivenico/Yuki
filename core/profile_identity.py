"""Stable, non-secret identity for one private Yuki desktop profile."""
from __future__ import annotations

import json
import os
import stat
import uuid
from pathlib import Path


_PROFILE_ID_FILENAME = "profile_identity.json"
_MAX_PROFILE_ID_BYTES = 1024


class ProfileIdentityError(RuntimeError):
    """Raised when a profile identity cannot be read or safely created."""


def _read_profile_id(path: Path) -> str:
    try:
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ProfileIdentityError("Yuki profile identity is not a private regular file.")
        if info.st_size <= 0 or info.st_size > _MAX_PROFILE_ID_BYTES:
            raise ProfileIdentityError("Yuki profile identity file has an invalid size.")
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags)
        try:
            chunks = []
            remaining = _MAX_PROFILE_ID_BYTES + 1
            while remaining:
                chunk = os.read(descriptor, min(remaining, 512))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            raw = b"".join(chunks)
        finally:
            os.close(descriptor)
        if len(raw) > _MAX_PROFILE_ID_BYTES:
            raise ProfileIdentityError("Yuki profile identity file exceeds its size limit.")
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict) or payload.get("schema_version") != 1:
            raise ValueError("invalid profile identity schema")
        value = payload.get("profile_id")
        parsed = uuid.UUID(value) if isinstance(value, str) else None
        if parsed is None or str(parsed) != value:
            raise ValueError("invalid profile identity")
        return str(parsed)
    except ProfileIdentityError:
        raise
    except FileNotFoundError:
        raise
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise ProfileIdentityError("Yuki profile identity is unreadable; preserve the profile and recover it before continuing.") from exc


def load_or_create_profile_id(profile_root: str | Path) -> str:
    """Read or atomically create a stable UUID; never replace an existing invalid file."""
    root = Path(profile_root).expanduser()
    if root.is_symlink() or not root.is_dir():
        raise ProfileIdentityError("Yuki profile directory is unavailable or unsafe.")
    path = root / _PROFILE_ID_FILENAME
    try:
        return _read_profile_id(path)
    except FileNotFoundError:
        pass
    profile_id = str(uuid.uuid4())
    payload = json.dumps(
        {"schema_version": 1, "profile_id": profile_id},
        sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags, 0o600)
    except FileExistsError:
        return _read_profile_id(path)
    except OSError as exc:
        raise ProfileIdentityError("Yuki could not safely create its profile identity.") from exc
    try:
        if os.name == "posix":
            os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        if os.name == "posix":
            directory_fd = os.open(root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    except Exception as exc:
        try:
            os.close(descriptor)
        except OSError:
            pass
        # Leave an incomplete identity in place rather than silently replacing it.
        raise ProfileIdentityError("Yuki could not durably create its profile identity.") from exc
    return profile_id


def profile_memory_keyring_account(profile_id: str) -> str:
    """Return a bounded, profile-scoped OS-keyring account identifier."""
    try:
        canonical = str(uuid.UUID(profile_id))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError("profile_id must be a canonical UUID.") from exc
    if canonical != profile_id:
        raise ValueError("profile_id must be a canonical UUID.")
    return "profile:" + canonical
