"""Stable, private owner identity for the local-only CLI conversation store."""
from __future__ import annotations

import os
import re
import secrets
import stat
from pathlib import Path

from core.portable_paths import data_root

_OWNER_ID = re.compile(r"^local-[A-Za-z0-9_-]{24,64}$")
_FILENAME = "local-owner-id"


def load_or_create_local_owner_id(root: str | Path | None = None) -> str:
    """Load or atomically create a non-secret local conversation ownership key.

    This identifier is not an authentication credential and is never accepted
    from network requests. It only scopes a local CLI process's SQLite records.
    """
    directory = Path(root).expanduser() if root is not None else data_root()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / _FILENAME

    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        fd = os.open(path, flags)
    except FileNotFoundError:
        value = "local-" + secrets.token_urlsafe(32)
        create_flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            create_flags |= os.O_NOFOLLOW
        try:
            fd = os.open(path, create_flags, 0o600)
        except FileExistsError:
            fd = os.open(path, flags)
        else:
            try:
                with os.fdopen(fd, "w", encoding="ascii") as stream:
                    stream.write(value + "\n")
                    stream.flush()
                    os.fsync(stream.fileno())
            except Exception:
                try:
                    path.unlink()
                except OSError:
                    pass
                raise
            return value

    try:
        metadata = os.fstat(fd)
        if not stat.S_ISREG(metadata.st_mode):
            raise RuntimeError("Local Yuki owner identity must be a regular file.")
        if os.name != "nt" and metadata.st_mode & 0o077:
            raise RuntimeError("Local Yuki owner identity permissions are too broad; restrict the file to its owner.")
        with os.fdopen(fd, "r", encoding="ascii") as stream:
            value = stream.read(256).strip()
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        raise
    if not _OWNER_ID.fullmatch(value):
        raise RuntimeError("Local Yuki owner identity file is invalid; do not delete it unless you intend to orphan local conversations.")
    return value
