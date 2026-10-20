"""Portable Yuki path helpers.

YUKI_HOME optionally points at the Yuki data root (for example, a folder on an
external SSD). Without it, data stays under the repository root. No secrets or
credentials are created by this module.
"""
from __future__ import annotations

import os
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def data_root() -> Path:
    configured = os.environ.get("YUKI_HOME", "").strip()
    root = Path(configured).expanduser() if configured else REPOSITORY_ROOT
    if not root.is_absolute():
        root = (REPOSITORY_ROOT / root).resolve()
    return root.resolve()


def config_path() -> Path:
    """Resolve the single runtime config entry point.

    Explicit YUKI_CONFIG wins. Otherwise prefer the user's onboarding config when
    it exists, retaining repository config.yaml as a backwards-compatible fallback.
    """
    configured = os.environ.get("YUKI_CONFIG", "").strip()
    if configured:
        path = Path(configured).expanduser()
        if not path.is_absolute():
            path = REPOSITORY_ROOT / path
        return path.resolve()

    onboarding_config = Path.home() / ".yuki" / "config.json"
    if onboarding_config.is_file():
        return onboarding_config.resolve()
    return (REPOSITORY_ROOT / "config.yaml").resolve()


def workspace_path(configured_workspace: str) -> Path:
    """Resolve relative workspace paths under YUKI_HOME when set."""
    value = Path(configured_workspace).expanduser()
    if value.is_absolute():
        return value.resolve()
    root = data_root()
    return (root / value).resolve()
