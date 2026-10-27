"""Construct the configured provider without implicit cloud fallback."""
from __future__ import annotations

import os

from providers.gemini_cli import GeminiCLIProvider
from providers.hybrid import HybridProvider
from providers.ollama import OllamaProvider
from providers.openai_compatible import OpenAICompatibleProvider


def create_provider(config=None, *, environ=None):
    """Build the configured real provider without implicit fallback.

    Ollama is the default so prompts stay local unless a cloud provider is explicitly selected. Hybrid routing keeps general tasks local and only
    routes explicitly allow-listed task names to cloud. API keys are read from
    the process environment, never from YAML or committed files. Mock providers
    are rejected by this runtime factory and must be injected directly by tests.
    """
    cfg = dict(config or {})
    env = os.environ if environ is None else environ
    selected = str(cfg.get("provider", "ollama")).strip().lower()
    if selected == "mock":
        raise ValueError("mock is test-only and cannot be selected by the runtime.")
    if selected == "openai":
        model = str(env.get("YUKI_OPENAI_MODEL", cfg.get("openai_model", "gpt-4.1-mini"))).strip()
        return OpenAICompatibleProvider(
            model,
            api_key_env="OPENAI_API_KEY",
            base_url=str(env.get("YUKI_OPENAI_BASE_URL", "https://api.openai.com/v1")),
            environ=env,
        )
    if selected == "gemini":
        return GeminiCLIProvider(
            timeout=float(env.get("YUKI_PROVIDER_TIMEOUT_SECONDS", 45)),
            max_prompt_chars=cfg.get("gemini_max_prompt_chars", 20000),
            max_output_chars=cfg.get("gemini_max_output_chars", 50000),
        )
    if selected not in {"ollama", "hybrid"}:
        raise ValueError("provider must be one of: openai, ollama, gemini, hybrid (mock is test-only).")
    local = OllamaProvider(
        base_url=str(env.get("YUKI_OLLAMA_BASE_URL", cfg.get("ollama_base_url", "http://127.0.0.1:11434"))),
        model=str(env.get("YUKI_OLLAMA_MODEL", cfg.get("ollama_model", "llama3.2:3b"))),
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
        )
    raw_tasks = str(env.get("YUKI_CLOUD_TASKS", "")).strip()
    cloud_tasks = [item.strip() for item in raw_tasks.split(",") if item.strip()]
    return HybridProvider(local, cloud, cloud_tasks=cloud_tasks)
