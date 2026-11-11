"""Private local-file path handling for personal Yuki data."""
from __future__ import annotations

import os
from pathlib import Path
import stat


class PrivateFileError(ValueError):
    """A private data file path is unsafe or cannot be secured."""


def prepare_private_file_path(path: str | Path) -> Path:
    """Create/tighten a regular single-link file without following final symlinks."""
    if not isinstance(path, (str, Path)) or not str(path).strip():
        raise PrivateFileError("private_file_path_required")
    requested = Path(path).expanduser()
    if requested.name in {"", ".", ".."}:
        raise PrivateFileError("private_file_path_invalid")
    try:
        parent = requested.parent.resolve()
        parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        target = parent / requested.name
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0)
        if os.name == "posix":
            flags |= getattr(os, "O_NOFOLLOW", 0)
        elif target.is_symlink():
            raise PrivateFileError("private_file_symlink_not_allowed")
        fd = os.open(target, flags, 0o600)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode):
                raise PrivateFileError("private_file_must_be_regular")
            if info.st_nlink != 1:
                raise PrivateFileError("private_file_hard_links_not_allowed")
            if os.name == "posix":
                os.fchmod(fd, 0o600)
        finally:
            os.close(fd)
        return target
    except PrivateFileError:
        raise
    except OSError as exc:
        raise PrivateFileError("private_file_cannot_be_secured") from exc


def read_private_file(path: str | Path, *, max_bytes: int) -> bytes:
    """Read through a no-follow descriptor and enforce file identity/type/size."""
    target = Path(path)
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    if os.name == "posix":
        flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(target, flags)
        with os.fdopen(fd, "rb") as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise PrivateFileError("private_file_identity_invalid")
            if info.st_size > max_bytes:
                raise PrivateFileError("private_file_size_limit_exceeded")
            data = handle.read(max_bytes + 1)
            if len(data) > max_bytes:
                raise PrivateFileError("private_file_size_limit_exceeded")
            return data
    except PrivateFileError:
        raise
    except OSError as exc:
        raise PrivateFileError("private_file_cannot_be_read") from exc
