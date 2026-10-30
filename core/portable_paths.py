"""Portable Yuki paths for source checkouts and frozen desktop distributions."""
from __future__ import annotations

import os
import platform
import shutil
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def bundled_root() -> Path:
    """Return the immutable PyInstaller bundle root, or source root in development."""
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent)).resolve()
    return REPOSITORY_ROOT


def _default_user_root() -> Path:
    system = platform.system()
    if system == "Windows":
        base = os.environ.get("LOCALAPPDATA")
        return Path(base).expanduser() / "Yuki" if base else Path.home() / "AppData" / "Local" / "Yuki"
    base = os.environ.get("XDG_DATA_HOME")
    return (Path(base).expanduser() if base else Path.home() / ".local" / "share") / "Yuki"


def data_root() -> Path:
    configured = os.environ.get("YUKI_HOME", "").strip()
    if configured:
        root = Path(configured).expanduser()
        if not root.is_absolute():
            anchor = Path.home() if is_frozen() else REPOSITORY_ROOT
            root = (anchor / root).resolve()
        return root.resolve()
    if is_frozen():
        return _default_user_root().resolve()
    return REPOSITORY_ROOT.resolve()


def config_path() -> Path:
    configured = os.environ.get("YUKI_CONFIG", "").strip()
    if configured:
        path = Path(configured).expanduser()
        if not path.is_absolute():
            anchor = data_root() if is_frozen() else REPOSITORY_ROOT
            path = (anchor / path).resolve()
        return path.resolve()
    if not is_frozen():
        return (REPOSITORY_ROOT / "config.yaml").resolve()

    destination = data_root() / "config.yaml"
    if not destination.exists():
        source = bundled_root() / "config.yaml"
        if not source.is_file():
            raise RuntimeError("Bundled default config.yaml is missing; set YUKI_CONFIG to a valid config file.")
        destination.parent.mkdir(parents=True, exist_ok=True)
        # Exclusive creation avoids overwriting an operator's config during concurrent startup.
        try:
            with source.open("rb") as source_file, destination.open("xb") as destination_file:
                shutil.copyfileobj(source_file, destination_file)
        except FileExistsError:
            pass
        except OSError as exc:
            raise RuntimeError("Could not initialize the per-user Yuki configuration.") from exc
    return destination.resolve()


def workspace_path(configured_workspace: str) -> Path:
    """Resolve relative workspace paths under YUKI_HOME when set."""
    value = Path(configured_workspace).expanduser()
    if value.is_absolute():
        return value.resolve()
    return (data_root() / value).resolve()
