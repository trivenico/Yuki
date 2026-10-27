"""Validated atomic persistence for desktop-editable Yuki settings."""
from __future__ import annotations
import os
import re
import tempfile
from pathlib import Path
from typing import Mapping
import yaml
from core.portable_paths import config_path

BOOLEAN_SETTINGS = ("voice_enabled", "security_lab_enabled", "sandbox_enabled", "gmail_enabled", "firewall_enabled", "firewall_dry_run", "wakeword_enabled", "plugin_discovery_enabled")
PROVIDERS = ("ollama", "openai", "gemini", "hybrid")
ALLOWED_SETTINGS = {"provider", "ollama_model", "openai_model", *BOOLEAN_SETTINGS}


def read_user_settings(path=None):
    target = Path(path) if path is not None else config_path()
    try:
        raw = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        raw = {}
    except yaml.YAMLError as exc:
        raise RuntimeError("Configuration YAML is invalid; settings were not changed.") from exc
    except OSError as exc:
        raise RuntimeError("Could not read Yuki settings.") from exc
    if not isinstance(raw, dict):
        raise RuntimeError("Configuration must be a YAML mapping.")
    result = {\n        "provider": raw.get("provider", "ollama"),\n        "ollama_model": raw.get("ollama_model", "llama3.2:3b"),\n        "openai_model": raw.get("openai_model", "gpt-4.1-mini"),\n    }\n    if result["provider"] not in PROVIDERS:\n        raise RuntimeError("Configured provider must be one of: " + ", ".join(PROVIDERS) + ".")\n    for model_key in ("ollama_model", "openai_model"):\n        if not isinstance(result[model_key], str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,119}", result[model_key]):\n            raise RuntimeError(f"Configured {model_key} is invalid.")
    for key in BOOLEAN_SETTINGS:
        result[key] = raw.get(key, key == "firewall_dry_run")
    if not isinstance(result["ollama_model"], str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,119}", result["ollama_model"]):
        raise RuntimeError("Configured Ollama model name is invalid.")
    for key in BOOLEAN_SETTINGS:
        if not isinstance(result[key], bool):
            raise RuntimeError(f"{key} must be true or false.")
    return result


def save_user_settings(changes: Mapping[str, object], path=None):
    if not isinstance(changes, Mapping) or not changes:
        raise ValueError("Provide at least one setting to change.")
    unknown = set(changes) - ALLOWED_SETTINGS
    if unknown:
        raise ValueError("Unsupported setting(s): " + ", ".join(sorted(unknown)))
    normalized = {}
    for key, value in changes.items():
        if key == "ollama_model":
            if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,119}", value.strip()):
                raise ValueError("Model must be a valid Ollama model name.")
            normalized[key] = value.strip()
        else:
            if not isinstance(value, bool):
                raise ValueError(f"{key} must be true or false.")
            normalized[key] = value
    target = Path(path) if path is not None else config_path()
    try:
        raw = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        raw = {}
    except yaml.YAMLError as exc:
        raise RuntimeError("Configuration YAML is invalid; settings were not changed.") from exc
    if not isinstance(raw, dict):
        raise RuntimeError("Configuration must be a YAML mapping.")
    raw.update(normalized)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary_name = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="\n", dir=target.parent, prefix=f".{target.name}.", suffix=".tmp", delete=False) as stream:
            temporary_name = stream.name
            yaml.safe_dump(raw, stream, sort_keys=False, allow_unicode=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, target)
    except OSError as exc:
        if temporary_name:
            try:
                os.unlink(temporary_name)
            except OSError:
                pass
        raise RuntimeError("Could not safely save Yuki settings.") from exc
    return read_user_settings(target)
