"""Small YAML configuration loader with safe environment overrides."""
import os
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config.yaml"


def _bounded_int(value, minimum, maximum):
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise RuntimeError(f"Expected an integer from {minimum} to {maximum}.")
    return value


def _bool_setting(env_name, raw, key, default=False):
    value = os.environ.get(env_name)
    if value is not None:
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
        raise RuntimeError(f"{env_name} must be true or false.")
    value = raw.get(key, default)
    if not isinstance(value, bool):
        raise RuntimeError(f"{key} must be true or false.")
    return value


def _string_list(raw, key):
    value = raw.get(key, [])
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        raise RuntimeError(f"{key} must be a list of non-empty strings.")
    if len(value) > 100:
        raise RuntimeError(f"{key} may contain at most 100 entries.")
    return list(dict.fromkeys(item.strip() for item in value))


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

    base_url = os.environ.get(
        "YUKI_OLLAMA_BASE_URL", str(raw.get("ollama_base_url", "http://127.0.0.1:11434"))
    ).strip().rstrip("/")
    if not base_url.startswith(("http://127.0.0.1", "http://localhost", "http://[::1]", "https://")):
        raise RuntimeError("Ollama URL must be loopback HTTP or HTTPS.")
    model = os.environ.get("YUKI_OLLAMA_MODEL", str(raw.get("ollama_model", ""))).strip()
    if len(model) > 120:
        raise RuntimeError("ollama_model is too long.")

    return {
        "name": "Yuki",
        "workspace": str(workspace.resolve()),
        "voice_enabled": _bool_setting("YUKI_VOICE_ENABLED", raw, "voice_enabled", False),
        "security_lab_enabled": _bool_setting("YUKI_SECURITY_LAB_ENABLED", raw, "security_lab_enabled", False),
        "sandbox_enabled": _bool_setting("YUKI_SANDBOX_ENABLED", raw, "sandbox_enabled", False),
        "recon_allowlisted_targets": _string_list(raw, "recon_allowlisted_targets"),
        "sandbox_allowed_images": _string_list(raw, "sandbox_allowed_images"),
        "provider": os.environ.get("YUKI_PROVIDER", str(raw.get("provider", "mock"))).strip().lower(),
        "ollama_base_url": base_url,
        "ollama_model": model,
        "stt_model": str(raw.get("stt_model", "small")),
        "tts_rate": _bounded_int(raw.get("tts_rate", 170), 80, 260),
        "max_agent_steps": _bounded_int(raw.get("max_agent_steps", 3), 1, 5),
    }
