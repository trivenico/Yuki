"""Interactive first-run provider preferences without collecting secrets."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from collections.abc import Callable

import yaml

from core.portable_paths import REPOSITORY_ROOT, config_path, user_config_path


PROVIDERS = {"openai", "ollama", "gemini"}


def _contains_secret_field(value: object) -> bool:
    """Detect credential-like YAML keys and refuse to rewrite them."""
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = str(key).strip().lower().replace("-", "_")
            if any(part in normalized for part in ("api_key", "secret", "password", "token", "credential")):
                return True
            if _contains_secret_field(child):
                return True
    elif isinstance(value, list):
        return any(_contains_secret_field(item) for item in value)
    return False


def save_provider_preferences(path: str | Path, provider: str, model: str = "") -> Path:
    """Atomically save non-secret provider preferences to a private user config."""
    provider = str(provider).strip().lower()
    if provider not in PROVIDERS:
        raise ValueError("Choose openai, ollama, or gemini.")
    model = str(model).strip()
    if len(model) > 120 or (provider in {"openai", "ollama"} and not model):
        raise ValueError("Model must be non-empty and at most 120 characters.")

    requested = Path(path).expanduser()
    if requested.is_symlink():
        raise RuntimeError("Refusing to write through a symbolic-link configuration file.")
    parent = requested.parent.resolve()
    target = parent / requested.name
    if target == REPOSITORY_ROOT or REPOSITORY_ROOT in target.parents:
        raise RuntimeError(
            "Provider setup will not modify files inside the source checkout. "
            "Set YUKI_CONFIG to a private user configuration path."
        )
    parent.mkdir(parents=True, exist_ok=True)
    if os.name == "posix":
        parent.chmod(0o700)

    raw: dict = {}
    if target.exists():
        if target.is_symlink() or not target.is_file():
            raise RuntimeError("Configuration path must be a regular file.")
        try:
            loaded = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as exc:
            raise RuntimeError("Existing configuration YAML is invalid; no changes were made.") from exc
        if not isinstance(loaded, dict):
            raise RuntimeError("Existing configuration must be a YAML mapping.")
        if _contains_secret_field(loaded):
            raise RuntimeError(
                "Existing configuration contains credential-like fields; move secrets to an environment "
                "variable or OS secret manager before running setup. No changes were made."
            )
        raw = loaded

    raw["provider"] = provider
    if provider == "openai":
        raw["openai_model"] = model
    elif provider == "ollama":
        raw["ollama_model"] = model

    descriptor, temporary_name = tempfile.mkstemp(prefix=".yuki-config-", suffix=".tmp", dir=str(parent))
    temporary = Path(temporary_name)
    try:
        if os.name == "posix":
            os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            yaml.safe_dump(raw, stream, sort_keys=False, allow_unicode=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
        if os.name == "posix":
            target.chmod(0o600)
            directory_fd = os.open(parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
    except Exception:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return target


def run_provider_setup(
    *, input_fn: Callable[[str], str] = input,
    output_fn: Callable[[str], object] = print,
    path: str | Path | None = None,
) -> int:
    """Prompt for provider/model only; credentials stay in the OS environment."""
    try:
        provider = input_fn("Provider [openai/ollama/gemini] (default openai): ").strip().lower() or "openai"
        if provider not in PROVIDERS:
            output_fn("Yuki setup: choose openai, ollama, or gemini.")
            return 2
        model = ""
        if provider == "openai":
            model = input_fn("OpenAI model [gpt-4.1-mini]: ").strip() or "gpt-4.1-mini"
        elif provider == "ollama":
            model = input_fn("Installed Ollama model name: ").strip()
            if not model:
                output_fn("Yuki setup: enter the name of an installed Ollama model.")
                return 2
        target = save_provider_preferences(
            path or (config_path() if os.environ.get("YUKI_CONFIG", "").strip() else user_config_path()),
            provider, model,
        )
    except (EOFError, KeyboardInterrupt):
        output_fn("Yuki setup cancelled; configuration was not changed.")
        return 2
    except Exception as exc:
        output_fn(f"Yuki setup failed ({type(exc).__name__}); configuration was not changed.")
        return 2

    output_fn(f"Yuki provider preferences saved to {target}.")
    if provider == "openai":
        output_fn("Set OPENAI_API_KEY in your shell or secret manager; setup never asks for it.")
    elif provider == "ollama":
        output_fn("Ensure Ollama is running and the selected model is installed.")
    else:
        output_fn("Ensure the Gemini CLI is installed and authenticated for this user.")
    output_fn("Run: yuki provider-status, then yuki provider-test when you consent to a live request.")
    return 0
