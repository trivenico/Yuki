"""Interactive first-run setup for Yuki.

Secrets are stored separately from config in a private local env file. For managed
production deployments, prefer an OS secret manager or injected environment vars.
"""
from __future__ import annotations

import getpass
import os
from pathlib import Path
import re

import yaml

from core.portable_paths import config_path, data_root


_ALLOWED_PROVIDERS = {"mock", "ollama", "hybrid"}
_SECRET_KEY = "OPENAI_API_KEY"


def _prompt(prompt: str, *, input_fn=input, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    value = input_fn(f"{prompt}{suffix}: ").strip()
    return value or default


def _read_config(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise RuntimeError("Existing Yuki configuration could not be read; no changes were made.") from exc
    if not isinstance(raw, dict):
        raise RuntimeError("Existing Yuki configuration must be a YAML mapping; no changes were made.")
    return raw


def _write_secret_env(path: Path, key: str, value: str) -> None:
    if not re.fullmatch(r"[A-Z][A-Z0-9_]{1,127}", key):
        raise ValueError("Invalid environment variable name.")
    if not value or any(ch in value for ch in "\r\n\x00"):
        raise ValueError("API key must be non-empty and cannot contain newline or NUL characters.")
    existing = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                name, secret = line.split("=", 1)
                if re.fullmatch(r"[A-Z][A-Z0-9_]{1,127}", name.strip()):
                    existing[name.strip()] = secret
    # API tokens are written as raw values, not shell-evaluated; reject syntax that
    # would be ambiguous to this intentionally small env-file parser.
    if any(ch.isspace() for ch in value) or "#" in value:
        raise ValueError("This setup accepts API keys without whitespace or '#'; set unusual tokens through the environment instead.")
    existing[key] = value
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text("# Yuki local credentials; do not commit this file.\n" + "".join(f"{k}={v}\n" for k, v in sorted(existing.items())), encoding="utf-8")
    try:
        os.chmod(temp, 0o600)
    except OSError:
        pass
    temp.replace(path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def run_setup(*, input_fn=input, secret_reader=getpass.getpass, config_file: Path | None = None,
              env_file: Path | None = None) -> str:
    """Run the setup wizard and return a safe, user-facing summary."""
    cfg_path = Path(config_file) if config_file else config_path()
    secrets_path = Path(env_file) if env_file else data_root() / ".env"
    cfg = _read_config(cfg_path)
    print("Yuki setup — choose how Yuki should connect to a model.")
    print("1) Mock/demo (no AI service)\n2) Local Ollama\n3) Hybrid local-first + explicitly allow-listed cloud tasks")
    choice = _prompt("Provider", input_fn=input_fn, default="2")
    mapping = {"1": "mock", "2": "ollama", "3": "hybrid", "mock": "mock", "ollama": "ollama", "hybrid": "hybrid"}
    provider = mapping.get(choice.lower())
    if provider not in _ALLOWED_PROVIDERS:
        raise ValueError("Choose 1, 2, or 3; configuration was not changed.")
    cfg["provider"] = provider
    if provider in {"ollama", "hybrid"}:
        cfg["ollama_base_url"] = _prompt("Ollama URL (local loopback recommended)", input_fn=input_fn,
                                          default=str(cfg.get("ollama_base_url", "http://127.0.0.1:11434")))
        cfg["ollama_model"] = _prompt("Installed Ollama model name", input_fn=input_fn,
                                       default=str(cfg.get("ollama_model", "")))
        if not cfg["ollama_model"]:
            raise ValueError("An Ollama model name is required; configuration was not changed.")
    if provider == "hybrid":
        print("Cloud requests are opt-in per task. Only tasks listed here can be routed to the provider.")
        model = _prompt("OpenAI-compatible cloud model ID", input_fn=input_fn, default=os.environ.get("YUKI_CLOUD_MODEL", ""))
        if not model:
            raise ValueError("A cloud model ID is required for hybrid mode; configuration was not changed.")
        tasks = _prompt("Allowed cloud task names (comma-separated; leave blank to disable cloud routing)",
                        input_fn=input_fn, default=os.environ.get("YUKI_CLOUD_TASKS", ""))
        cfg["provider"] = "hybrid"
        key = secret_reader("Cloud API key (input hidden; blank keeps environment/existing key): ").strip()
        if key:
            _write_secret_env(secrets_path, _SECRET_KEY, key)
        # Persist non-secret provider settings in config; task routing stays in env
        # so deployments can manage the data-egress policy separately.
        os.environ["YUKI_CLOUD_MODEL"] = model
        os.environ["YUKI_CLOUD_TASKS"] = tasks
        env_values = {"YUKI_CLOUD_MODEL": model, "YUKI_CLOUD_TASKS": tasks,
                      "YUKI_CLOUD_API_KEY_ENV": _SECRET_KEY}
        _write_nonsecret_env(secrets_path, env_values)
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    rendered = yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True)
    temp_cfg = cfg_path.with_suffix(cfg_path.suffix + ".tmp")
    temp_cfg.write_text(rendered, encoding="utf-8")
    temp_cfg.replace(cfg_path)
    return (f"Saved provider configuration to {cfg_path}.\n"
            + (f"Local credentials/config saved separately to {secrets_path} (permissions set to owner-only where supported).\n" if provider == "hybrid" else "")
            + "Run `python main.py` and use `/health` to check readiness. Setup does not prove the provider is reachable.")


def _write_nonsecret_env(path: Path, values: dict[str, str]) -> None:
    """Merge non-secret settings into the local env file without shell evaluation."""
    existing = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                if re.fullmatch(r"[A-Z][A-Z0-9_]{1,127}", key.strip()):
                    existing[key.strip()] = value
    for key, value in values.items():
        if "\n" in value or "\r" in value or "\x00" in value:
            raise ValueError(f"Invalid value for {key}.")
        existing[key] = value
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text("# Yuki local settings; do not commit this file.\n" + "".join(f"{k}={v}\n" for k, v in sorted(existing.items())), encoding="utf-8")
    try:
        os.chmod(temp, 0o600)
    except OSError:
        pass
    temp.replace(path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
