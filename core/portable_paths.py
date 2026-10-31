"""Portable Yuki path helpers.

YUKI_HOME optionally points at the Yuki data root (for example, a folder on an
external SSD). Without it, data stays under the repository root. No secrets or
credentials are created by this module.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def data_root() -> Path:
    """Return a writable per-user data directory unless YUKI_HOME overrides it."""
    configured = os.environ.get("YUKI_HOME", "").strip()
    if configured:
        root = Path(configured).expanduser()
        if not root.is_absolute():
            root = REPOSITORY_ROOT / root
        return root.resolve()

    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        root = Path(base).expanduser() / "Yuki" if base else Path.home() / "AppData" / "Local" / "Yuki"
    elif sys.platform == "darwin":
        root = Path.home() / "Library" / "Application Support" / "Yuki"
    else:
        base = os.environ.get("XDG_DATA_HOME")
        root = (Path(base).expanduser() / "Yuki") if base else Path.home() / ".local" / "share" / "Yuki"
    return root.resolve()


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
