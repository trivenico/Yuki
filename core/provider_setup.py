"""Interactive first-run provider preferences without collecting secrets."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from collections.abc import Callable

import yaml

from core.portable_paths import REPOSITORY_ROOT, config_path, ensure_private_directory, user_config_path


PROVIDERS = {"openai", "ollama", "gemini", "llama_cpp"}


def _contains_secret_field(value: object, _seen: set[int] | None = None) -> bool:
    """Detect credential-like YAML keys, including safely handling YAML aliases."""
    if _seen is None:
        _seen = set()
    if isinstance(value, (dict, list)):
        identity = id(value)
        if identity in _seen:
            return False
        _seen.add(identity)
    if isinstance(value, dict):
        for key, child in value.items():
            normalized = str(key).strip().lower().replace("-", "_")
            if any(part in normalized for part in ("api_key", "secret", "password", "token", "credential")):
                return True
            if _contains_secret_field(child, _seen):
                return True
    elif isinstance(value, list):
        return any(_contains_secret_field(item, _seen) for item in value)
    return False


def save_provider_preferences(path: str | Path, provider: str, model: str = "") -> Path:
    """Atomically save non-secret provider preferences to a private user config."""
    provider = str(provider).strip().lower()
    if provider not in PROVIDERS:
        raise ValueError("Choose openai, ollama, or gemini.")
    model = str(model).strip()
    max_model_length = 4096 if provider == "llama_cpp" else 120
    if len(model) > max_model_length or (provider in {"openai", "ollama", "llama_cpp"} and not model):
        raise ValueError("Model or local model path is empty or exceeds its size limit.")
    if provider == "llama_cpp":
        model_file = Path(model).expanduser()
        if model_file.is_symlink() or not model_file.is_file() or model_file.suffix.lower() != ".gguf":
            raise ValueError("Choose an existing regular local GGUF model file, not a symbolic link.")

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
    ensure_private_directory(parent)

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
    elif provider == "llama_cpp":
        raw["llama_cpp_model_path"] = str(Path(model).expanduser().resolve(strict=True))

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
        provider = input_fn("Provider [openai/ollama/llama_cpp/gemini] (default openai): ").strip().lower() or "openai"
        if provider not in PROVIDERS:
            output_fn("Yuki setup: choose openai, ollama, llama_cpp, or gemini.")
            return 2
        model = ""
        if provider == "openai":
            model = input_fn("OpenAI model [gpt-4.1-mini]: ").strip() or "gpt-4.1-mini"
        elif provider == "ollama":
            model = input_fn("Installed Ollama model name: ").strip()
            if not model:
                output_fn("Yuki setup: enter the name of an installed Ollama model.")
                return 2
        elif provider == "llama_cpp":
            model = input_fn("Absolute path to an existing local GGUF model: ").strip()
            if not model:
                output_fn("Yuki setup: enter a local GGUF model path.")
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
    elif provider == "llama_cpp":
        output_fn("Install the optional runtime with: python -m pip install -e \".[local-inference]\"")
        output_fn("Yuki will load the local model in-process; no Ollama server or hosted inference API is used.")
    else:
        output_fn("Ensure the Gemini CLI is installed and authenticated for this user.")
    output_fn("Run: yuki provider-status, then yuki provider-test when you consent to a live request.")
    return 0
