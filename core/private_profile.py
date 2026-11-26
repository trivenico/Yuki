"""Owner-private application profile directory validation.

This module is deliberately not wired into CLI or desktop startup yet. The
migration and cross-platform ACL contracts must be completed before existing
user data is moved or storage defaults are changed.
"""
from __future__ import annotations

import os
from pathlib import Path
import stat


class PrivateProfileError(RuntimeError):
    """The application profile directory cannot be proven private."""


def ensure_private_profile_directory(path: str | Path) -> Path:
    """Create or validate one owner-private profile directory.

    POSIX: the final directory must be a real directory owned by the effective
    user and have no group/other permission bits. A newly-created directory is
    opened without following a final-component symlink and set to mode 0700.

    Windows ACL verification is intentionally not inferred from POSIX mode
    bits; a dedicated ACL adapter and acceptance tests are required before
    this helper can be used as a Windows security boundary.
    """
    if not isinstance(path, (str, Path)) or not str(path).strip():
        raise PrivateProfileError("private_profile_path_required")
    if os.name != "posix":
        raise PrivateProfileError("private_profile_acl_verification_unavailable")

    target = Path(path).expanduser()
    if not target.is_absolute():
        target = Path.cwd() / target
    target = Path(os.path.abspath(target))
    if target == Path(target.anchor):
        raise PrivateProfileError("private_profile_root_invalid")

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.mkdir(target, 0o700)
        except FileExistsError:
            pass

        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(target, flags)
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISDIR(info.st_mode):
                raise PrivateProfileError("private_profile_not_directory")
            if info.st_uid != os.geteuid():
                raise PrivateProfileError("private_profile_owner_mismatch")
            if stat.S_IMODE(info.st_mode) & 0o077:
                raise PrivateProfileError("private_profile_permissions_too_broad")
        finally:
            os.close(descriptor)
    except PrivateProfileError:
        raise
    except OSError as exc:
        raise PrivateProfileError("private_profile_cannot_be_secured") from exc

    return target
