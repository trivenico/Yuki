"""Private filesystem boundary for native desktop application state."""
from __future__ import annotations

import os
import stat
from pathlib import Path


class DesktopStorageError(RuntimeError):
    """Raised when the desktop cannot establish a private data directory."""


def prepare_private_desktop_root(root: Path) -> Path:
    """Create and verify the desktop data root before opening any local stores.

    POSIX installations require owner-only directory permissions. Windows
    relies on the current user's application-data ACL and never receives a
    misleading chmod-based security claim.
    """
    path = Path(root).expanduser()
    if path.is_symlink():
        raise DesktopStorageError("Desktop data root must not be a symbolic link.")
    try:
        path.mkdir(parents=True, exist_ok=True)
        if not path.is_dir():
            raise DesktopStorageError("Desktop data root is not a directory.")
        if os.name == "posix":
            path.chmod(0o700)
            mode = stat.S_IMODE(path.stat().st_mode)
            if mode != 0o700:
                raise DesktopStorageError(
                    "Desktop data root permissions are not private (expected mode 0700)."
                )
            if hasattr(os, "geteuid") and path.stat().st_uid != os.geteuid():
                raise DesktopStorageError("Desktop data root is not owned by the current user.")
    except DesktopStorageError:
        raise
    except OSError as exc:
        raise DesktopStorageError(
            "Yuki cannot establish private permissions for its desktop data directory."
        ) from exc
    return path.resolve()


__all__ = ["DesktopStorageError", "prepare_private_desktop_root"]
