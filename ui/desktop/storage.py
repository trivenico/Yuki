"""Private filesystem boundary for native desktop application state."""
from __future__ import annotations

import os
import stat
import tempfile
from pathlib import Path


class DesktopStorageError(RuntimeError):
    """Raised when the desktop cannot establish a private data directory."""


def _is_separate_volume(path: Path) -> bool:
    """Check whether portable storage is on a distinct mounted volume."""
    try:
        anchor = path.parent
        if os.name == "posix":
            return anchor.stat().st_dev != anchor.parent.stat().st_dev
        if os.name == "nt":
            configured_drive = os.path.splitdrive(str(anchor))[0]
            system_drive = os.environ.get("SystemDrive", "C:")
            if not configured_drive:
                return False
            configured_root = Path(configured_drive + "\\\\")
            system_root = Path(system_drive + "\\\\")
            return configured_root.stat().st_dev != system_root.stat().st_dev
    except OSError:
        return False
    return False


def prepare_private_desktop_root(
    root: Path, *, require_existing_parent: bool = False,
    require_separate_volume: bool = False,
) -> Path:
    """Create and verify the desktop data root before opening any local stores.

    POSIX installations require owner-only directory permissions. Windows
    relies on the current user's application-data ACL and never receives a
    misleading chmod-based security claim.
    """
    path = Path(root).expanduser()
    if require_existing_parent and not path.parent.is_dir():
        raise DesktopStorageError(
            "Configured YUKI_HOME is unavailable; refusing to create data on another filesystem."
        )
    if require_separate_volume and not _is_separate_volume(path):
        raise DesktopStorageError(
            "Configured YUKI_HOME is not on a separately mounted volume; refusing portable startup."
        )
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
    # A successful mkdir/stat does not prove the volume is writable. Probe
    # the exact directory Yuki will use before any SQLite store is initialized.
    probe_path = None
    try:
        descriptor, probe_name = tempfile.mkstemp(prefix=".yuki-write-test-", dir=str(path))
        probe_path = Path(probe_name)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        probe_path.unlink()
        probe_path = None
    except OSError as exc:
        if probe_path is not None:
            try:
                probe_path.unlink(missing_ok=True)
            except OSError:
                pass
        raise DesktopStorageError(
            "Yuki data storage is not writable; check the drive and filesystem permissions."
        ) from exc
    return path.resolve()


__all__ = ["DesktopStorageError", "prepare_private_desktop_root"]
