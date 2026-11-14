"""Portable Yuki config and data locations.

A source checkout keeps its existing repository-local defaults. An installed
application stores mutable configuration and data in the user's OS config/data
directories unless YUKI_CONFIG or YUKI_HOME explicitly overrides them.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def _user_config_root() -> Path:
    if os.name == "nt":
        base = os.environ.get("APPDATA")
        return Path(base).expanduser() / "Yuki" if base else Path.home() / "AppData" / "Roaming" / "Yuki"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Yuki"
    base = os.environ.get("XDG_CONFIG_HOME", "").strip()
    return (Path(base).expanduser() if base else Path.home() / ".config") / "yuki"


def _user_data_root() -> Path:
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        return Path(base).expanduser() / "Yuki" if base else Path.home() / "AppData" / "Local" / "Yuki"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Yuki" / "Data"
    base = os.environ.get("XDG_DATA_HOME", "").strip()
    return (Path(base).expanduser() if base else Path.home() / ".local" / "share") / "yuki"


def data_root() -> Path:
    configured = os.environ.get("YUKI_HOME", "").strip()
    if configured:
        root = Path(configured).expanduser()
        if not root.is_absolute():
            root = (REPOSITORY_ROOT / root).resolve()
        return root.resolve()
    # Preserve repository-local behavior for developers; installed wheels use
    # user-owned writable storage rather than site-packages.
    if (REPOSITORY_ROOT / "config.yaml").is_file():
        return REPOSITORY_ROOT.resolve()
    return _user_data_root().resolve()



def validate_configured_data_root() -> Path | None:
    """Fail closed when an explicitly selected portable data volume is unavailable.

    An explicit YUKI_HOME is an operator choice: silently falling back to another
    directory risks splitting memory and application state across machines.
    The launcher/default path remains lazily created by normal workspace setup.
    """
    configured = os.environ.get("YUKI_HOME", "").strip()
    if not configured:
        return None
    root = data_root()
    if not root.exists():
        raise RuntimeError(
            f"Configured YUKI_HOME is unavailable: {root}. "
            "Reconnect the selected drive or correct YUKI_HOME; Yuki will not use another data directory."
        )
    if not root.is_dir():
        raise RuntimeError(f"Configured YUKI_HOME is not a directory: {root}.")
    descriptor = None
    probe_path = None
    try:
        descriptor, probe_path = tempfile.mkstemp(prefix=".yuki-write-check-", dir=str(root))
    except OSError as exc:
        raise RuntimeError(
            f"Configured YUKI_HOME is not writable: {root}. "
            "Check that the drive is mounted and has write access."
        ) from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if probe_path is not None:
            try:
                os.unlink(probe_path)
            except OSError as exc:
                raise RuntimeError(
                    f"Could not clean up YUKI_HOME write check in {root}; inspect the directory before retrying."
                ) from exc
    return root


def config_path() -> Path:
    configured = os.environ.get("YUKI_CONFIG", "").strip()
    if configured:
        path = Path(configured).expanduser()
        if not path.is_absolute():
            path = (REPOSITORY_ROOT / path).resolve()
        return path.resolve()
    source_config = REPOSITORY_ROOT / "config.yaml"
    if source_config.is_file():
        return source_config.resolve()
    return (_user_config_root() / "config.yaml").resolve()


def workspace_path(configured_workspace: str) -> Path:
    """Resolve relative workspace paths under YUKI_HOME or the user data root."""
    value = Path(configured_workspace).expanduser()
    if value.is_absolute():
        return value.resolve()
    return (data_root() / value).resolve()
