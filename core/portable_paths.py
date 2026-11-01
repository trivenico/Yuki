"""Portable Yuki path helpers.

YUKI_HOME optionally points at the Yuki data root (for example, a folder on an
external SSD). CLI defaults remain repository-local for backward compatibility.
The desktop uses an OS user-data directory unless YUKI_HOME is explicitly set.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def data_root() -> Path:
    configured = os.environ.get("YUKI_HOME", "").strip()
    root = Path(configured).expanduser() if configured else REPOSITORY_ROOT
    if not root.is_absolute():
        root = (REPOSITORY_ROOT / root).resolve()
    return root.resolve()


def desktop_data_root() -> Path:
    """Return the desktop's private per-user data directory.

    An explicit YUKI_HOME keeps portable deployments predictable and isolated
    from the OS profile. Without it, use the platform's conventional writable
    application-data location rather than the installation/bundle directory.
    """
    configured = os.environ.get("YUKI_HOME", "").strip()
    if configured:
        return (data_root() / ".yuki").resolve()

    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        root = Path(base).expanduser() if base else Path.home() / "AppData" / "Local"
    elif sys.platform == "darwin":
        root = Path.home() / "Library" / "Application Support"
    else:
        base = os.environ.get("XDG_DATA_HOME", "").strip()
        root = Path(base).expanduser() if base else Path.home() / ".local" / "share"
    return (root / "Yuki").resolve()


def config_path() -> Path:
    configured = os.environ.get("YUKI_CONFIG", "").strip()
    path = Path(configured).expanduser() if configured else REPOSITORY_ROOT / "config.yaml"
    if not path.is_absolute():
        path = (REPOSITORY_ROOT / path).resolve()
    return path.resolve()


def workspace_path(configured_workspace: str) -> Path:
    """Resolve relative workspace paths under YUKI_HOME when set."""
    value = Path(configured_workspace).expanduser()
    if value.is_absolute():
        return value.resolve()
    root = data_root()
    return (root / value).resolve()
