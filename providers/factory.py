"""Construct the configured provider without implicit cloud fallback."""
from __future__ import annotations

import os

from providers.gemini_cli import GeminiCLIProvider
from providers.hybrid import HybridProvider
from providers.mock import MockProvider
from providers.ollama import OllamaProvider
from providers.openai_compatible import OpenAICompatibleProvider


def _provider_timeout(env):
    """Return the validated network timeout shared by configured providers."""
    raw = env.get("YUKI_PROVIDER_TIMEOUT_SECONDS", "45")
    try:
        timeout = float(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("YUKI_PROVIDER_TIMEOUT_SECONDS must be a number between 1 and 120.") from exc
    if not 1 <= timeout <= 120:
        raise ValueError("YUKI_PROVIDER_TIMEOUT_SECONDS must be between 1 and 120.")
    return timeout


def create_provider(config=None, *, environ=None):
    """Build mock, local Ollama, or hybrid routing from validated config.

    Cloud routing is opt-in through YUKI_CLOUD_TASKS. The general/default task
    remains local. No provider API key is read from YAML or committed files.
    """
    cfg = dict(config or {})
    env = os.environ if environ is None else environ
    selected = str(cfg.get("provider", "mock")).strip().lower()
    if selected == "mock":
        return MockProvider()
    if selected == "gemini_cli":
        return GeminiCLIProvider(timeout=_provider_timeout(env))
    if selected not in {"ollama", "hybrid"}:
        raise ValueError("provider must be one of: mock, ollama, hybrid, gemini_cli.")
    timeout = _provider_timeout(env)
    local = OllamaProvider(
        base_url=str(cfg.get("ollama_base_url", "http://127.0.0.1:11434")),
        model=str(cfg.get("ollama_model", env.get("YUKI_OLLAMA_MODEL", ""))),
        timeout=timeout,
    )
    if selected == "ollama":
        return local
    if selected == "hybrid":
        cloud_model = str(env.get("YUKI_CLOUD_MODEL", "")).strip()
        cloud = None
        if cloud_model:
            cloud = OpenAICompatibleProvider(
                cloud_model,
                api_key_env=str(env.get("YUKI_CLOUD_API_KEY_ENV", "OPENAI_API_KEY")),
                base_url=str(env.get("YUKI_CLOUD_BASE_URL", "https://api.openai.com/v1")),
                timeout=timeout,
            )
        raw_tasks = str(env.get("YUKI_CLOUD_TASKS", "")).strip()
        cloud_tasks = [item.strip() for item in raw_tasks.split(",") if item.strip()]
        return HybridProvider(local, cloud, cloud_tasks=cloud_tasks)
