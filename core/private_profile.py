"""Owner-private application profile directory validation.

This validator is used by the control-plane composition and maintenance fence.
The installed CLI conversation store remains outside this boundary. The
migration and cross-platform ACL contracts must be completed before existing
user data is moved or storage defaults are changed.
"""
from __future__ import annotations

import os
from pathlib import Path
import stat


class PrivateProfileError(RuntimeError):
    """The application profile directory cannot be proven private."""


def _check_ancestor(info: os.stat_result, *, final: bool) -> None:
    if not stat.S_ISDIR(info.st_mode):
        raise PrivateProfileError("private_profile_not_directory")
    owner = os.geteuid()
    if final:
        if info.st_uid != owner:
            raise PrivateProfileError("private_profile_owner_mismatch")
        if stat.S_IMODE(info.st_mode) & 0o077:
            raise PrivateProfileError("private_profile_permissions_too_broad")
        return

    # Ancestors may be root-owned system directories (for example /home) or
    # owned by this user. Reject unexpected ownership and writable shared
    # ancestors. The conventional root-owned sticky /tmp is the sole exception.
    if info.st_uid not in {0, owner}:
        raise PrivateProfileError("private_profile_ancestor_owner_mismatch")
    mode = stat.S_IMODE(info.st_mode)
    root_sticky = info.st_uid == 0 and bool(mode & stat.S_ISVTX)
    if mode & 0o022 and not root_sticky:
        raise PrivateProfileError("private_profile_ancestor_permissions_too_broad")


def ensure_private_profile_directory(path: str | Path) -> Path:
    """Create or validate a private profile using a no-follow directory walk.

    On POSIX, each path component is opened relative to its already-open parent
    with O_NOFOLLOW. Missing components are created as 0700. Existing ancestor
    directories must be root- or current-user-owned and not group/other
    writable, except the conventional root-owned sticky temporary directory.
    The final directory must be current-user-owned and exactly owner-accessible.

    Windows ACL verification is not inferred from POSIX mode bits. This
    prototype fails closed on Windows until a dedicated DACL adapter and
    acceptance tests exist.
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

    if (
        not hasattr(os, "O_DIRECTORY")
        or not hasattr(os, "O_NOFOLLOW")
        or os.open not in getattr(os, "supports_dir_fd", set())
        or os.mkdir not in getattr(os, "supports_dir_fd", set())
    ):
        raise PrivateProfileError("private_profile_safe_directory_walk_unavailable")
    flags = os.O_RDONLY | os.O_DIRECTORY
    flags |= getattr(os, "O_CLOEXEC", 0) | os.O_NOFOLLOW
    descriptors: list[int] = []
    try:
        current_fd = os.open(target.anchor, flags)
        descriptors.append(current_fd)
        components = target.parts[1:]
        for index, component in enumerate(components):
            is_final = index == len(components) - 1
            try:
                next_fd = os.open(component, flags, dir_fd=current_fd)
            except FileNotFoundError:
                try:
                    os.mkdir(component, 0o700, dir_fd=current_fd)
                except FileExistsError:
                    # Another process may have created it; open and validate
                    # the object actually present rather than trusting it.
                    pass
                next_fd = os.open(component, flags, dir_fd=current_fd)
            info = os.fstat(next_fd)
            _check_ancestor(info, final=is_final)
            descriptors.append(next_fd)
            current_fd = next_fd
    except PrivateProfileError:
        raise
    except OSError as exc:
        raise PrivateProfileError("private_profile_cannot_be_secured") from exc
    finally:
        for descriptor in reversed(descriptors):
            try:
                os.close(descriptor)
            except OSError:
                pass

    return target
