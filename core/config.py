"""Small YAML configuration loader with safe environment overrides."""
import os
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config.yaml"


def load_config(path=None):
    config_path = Path(path) if path else DEFAULT_CONFIG
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except OSError as exc:
        raise RuntimeError(f"Cannot read configuration file: {config_path}") from exc
    except yaml.YAMLError as exc:
        raise RuntimeError("Configuration YAML is invalid.") from exc
    if not isinstance(raw, dict):
        raise RuntimeError("Configuration must be a YAML mapping.")

    workspace_value = os.environ.get("YUKI_WORKSPACE", raw.get("workspace", "./yuki_workspace"))
    if not isinstance(workspace_value, str) or not workspace_value.strip():
        raise RuntimeError("workspace must be a non-empty path.")
    workspace = Path(workspace_value).expanduser()
    if not workspace.is_absolute():
        workspace = ROOT / workspace
    return {
        "name": "Yuki",
        "workspace": str(workspace.resolve()),
        "voice_enabled": False,
        "provider": os.environ.get("YUKI_PROVIDER", str(raw.get("provider", "mock"))).strip().lower(),
        "ollama_base_url": os.environ.get(
            "YUKI_OLLAMA_BASE_URL", str(raw.get("ollama_base_url", "http://127.0.0.1:11434"))
        ).rstrip("/"),
        "ollama_model": os.environ.get("YUKI_OLLAMA_MODEL", str(raw.get("ollama_model", ""))).strip(),
        "max_agent_steps": _bounded_int(raw.get("max_agent_steps", 3), 1, 5),
    }


def _bounded_int(value, minimum, maximum):
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise RuntimeError(f"Expected an integer from {minimum} to {maximum}.")
    return value
