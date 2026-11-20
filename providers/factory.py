"""Construct the selected provider; native core is the no-network default."""
from __future__ import annotations

import os

from providers.gemini_cli import GeminiCLIProvider
from providers.hybrid import HybridProvider
from providers.mock import MockProvider
from providers.native_core import NativeCoreAdapter
from providers.ollama import OllamaProvider
from providers.openai_compatible import OpenAICompatibleProvider


def create_provider(config=None, *, environ=None):
    """Build an explicitly selected adapter without implicit cloud fallback.

    Native core is the default and requires no credentials or network access.
    External providers are opt-in. API keys are read from the process
    environment, never from YAML or committed files. The mock adapter remains
    available only to tests that call this factory explicitly.
    """
    cfg = dict(config or {})
    selected = str(cfg.get("provider", "native")).strip().lower()
    if selected in {"native", "none", "native-only"}:
        return NativeCoreAdapter()

    env = os.environ if environ is None else environ
    try:
        timeout = float(env.get("YUKI_PROVIDER_TIMEOUT_SECONDS", cfg.get("provider_timeout_seconds", 45)))
    except (TypeError, ValueError) as exc:
        raise ValueError("YUKI_PROVIDER_TIMEOUT_SECONDS must be a number from 1 to 120.") from exc
    raw_timeout = env.get("YUKI_PROVIDER_TIMEOUT_SECONDS", cfg.get("provider_timeout_seconds", 45))
    if isinstance(raw_timeout, bool) or not 1 <= timeout <= 120:
        raise ValueError("Provider Timeout must be between 1 and 120 seconds.")
    if selected == "mock":
        return MockProvider()
    if selected == "openai":
        model = str(env.get("YUKI_OPENAI_MODEL", cfg.get("openai_model", "gpt-4.1-mini"))).strip()
        return OpenAICompatibleProvider(
            model,
            api_key_env="OPENAI_API_KEY",
            base_url=str(env.get("YUKI_OPENAI_BASE_URL", "https://api.openai.com/v1")),
            timeout=timeout,
            environ=env,
        )
    if selected == "gemini":
        return GeminiCLIProvider(
            timeout=timeout,
            max_prompt_chars=cfg.get("gemini_max_prompt_chars", 20000),
            max_output_chars=cfg.get("gemini_max_output_chars", 50000),
        )
    if selected not in {"ollama", "hybrid"}:
        raise ValueError("provider must be one of: native, openai, ollama, gemini, hybrid (mock is test-only).")
    local = OllamaProvider(
        base_url=str(cfg.get("ollama_base_url", "http://127.0.0.1:11434")),
        model=str(cfg.get("ollama_model", env.get("YUKI_OLLAMA_MODEL", ""))),
        timeout=timeout,
    )
    if selected == "ollama":
        return local
    cloud_model = str(env.get("YUKI_CLOUD_MODEL", "")).strip()
    cloud = None
    if cloud_model:
        cloud = OpenAICompatibleProvider(
            cloud_model,
            api_key_env=str(env.get("YUKI_CLOUD_API_KEY_ENV", "OPENAI_API_KEY")),
            base_url=str(env.get("YUKI_CLOUD_BASE_URL", "https://api.openai.com/v1")),
            timeout=timeout,
            environ=env,
        )
    raw_tasks = str(env.get("YUKI_CLOUD_TASKS", "")).strip()
    cloud_tasks = [item.strip() for item in raw_tasks.split(",") if item.strip()]
    return HybridProvider(local, cloud, cloud_tasks=cloud_tasks, owns_providers=True)
