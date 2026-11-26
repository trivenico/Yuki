"""Portable Yuki config and data locations.

A source checkout keeps its existing repository-local defaults. An installed
application stores mutable configuration and data in the user's OS config/data
directories unless YUKI_CONFIG or YUKI_HOME explicitly overrides them.
"""
from __future__ import annotations

import os
import sys
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


def desktop_data_root() -> Path:
    """Return the lexical desktop-state directory beneath the application root.

    Deliberately do not resolve this final path: security-sensitive callers
    must be able to detect a symlink at .yuki rather than silently following it.
    """
    return data_root() / ".yuki"


def private_profile_root() -> Path:
    """Return the canonical owner-private profile path without creating it.

    Source checkouts and explicit YUKI_HOME installations use the application
    root's .yuki/private-profile directory. Installed builds use the OS data
    root's equivalent location. This is a path policy only; callers must pass
    it through the platform security validator before storing private data.
    """
    return desktop_data_root() / "private-profile"


def ensure_private_directory(path: str | Path) -> Path:
    """Create missing directory components with owner-only POSIX permissions.

    Existing directories are deliberately left unchanged. Creating each
    missing component separately avoids pathlib's parents=True behavior, which
    can create intermediate directories using the process default mode.
    """
    target = Path(path).expanduser().resolve()
    missing: list[Path] = []
    current = target
    while not current.exists():
        missing.append(current)
        parent = current.parent
        if parent == current:
            raise OSError("Cannot find an existing ancestor for the private directory.")
        current = parent
    if not current.is_dir():
        raise NotADirectoryError(str(current))
    for directory in reversed(missing):
        try:
            directory.mkdir(mode=0o700)
        except FileExistsError:
            if not directory.is_dir():
                raise
    return target


def user_config_path() -> Path:
    """Return the per-user configuration path, independent of checkout files."""
    return (_user_config_root() / "config.yaml").resolve()


def config_path() -> Path:
    configured = os.environ.get("YUKI_CONFIG", "").strip()
    if configured:
        path = Path(configured).expanduser()
        if not path.is_absolute():
            path = (REPOSITORY_ROOT / path).resolve()
        return path.resolve()
    user_config = _user_config_root() / "config.yaml"
    if user_config.is_file():
        return user_config.resolve()
    source_config = REPOSITORY_ROOT / "config.yaml"
    if source_config.is_file():
        return source_config.resolve()
    return user_config.resolve()


def workspace_path(configured_workspace: str) -> Path:
    """Resolve relative workspace paths under YUKI_HOME or the user data root."""
    value = Path(configured_workspace).expanduser()
    if value.is_absolute():
        return value.resolve()
    return (data_root() / value).resolve()
