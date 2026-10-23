"""Construct the configured provider without implicit cloud fallback."""
from __future__ import annotations

import os

from providers.gemini_cli import GeminiCLIProvider
from providers.hybrid import HybridProvider
from providers.mock import MockProvider
from providers.ollama import OllamaProvider
from providers.openai_compatible import OpenAICompatibleProvider


def create_provider(config=None, *, environ=None):
    """Build mock, local Ollama, Gemini CLI, or hybrid routing from config.

    Cloud routing is opt-in through YUKI_CLOUD_TASKS. The general/default task
    remains local. No provider API key is read from YAML or committed files.
    """
    cfg = dict(config or {})
    env = os.environ if environ is None else environ
    selected = str(cfg.get("provider", "mock")).strip().lower()
    if selected == "mock":
        return MockProvider()
    if selected == "gemini":
        return GeminiCLIProvider(
            timeout=float(env.get("YUKI_PROVIDER_TIMEOUT_SECONDS", 45)),
            max_prompt_chars=cfg.get("gemini_max_prompt_chars", 20000),
            max_output_chars=cfg.get("gemini_max_output_chars", 50000),
        )
    compatible = {
        "openai": ("OPENAI_API_KEY", "https://api.openai.com/v1", "YUKI_OPENAI_MODEL"),
        "deepseek": ("DEEPSEEK_API_KEY", "https://api.deepseek.com/v1", "YUKI_DEEPSEEK_MODEL"),
        "kimi": ("MOONSHOT_API_KEY", "https://api.moonshot.ai/v1", "YUKI_KIMI_MODEL"),
        "minimax": ("MINIMAX_API_KEY", "https://api.minimax.chat/v1", "YUKI_MINIMAX_MODEL"),
        "openrouter": ("OPENROUTER_API_KEY", "https://openrouter.ai/api/v1", "YUKI_OPENROUTER_MODEL"),
        "tokenrouter": ("TOKENROUTER_API_KEY", "https://api.tokenrouter.io/v1", "YUKI_TOKENROUTER_MODEL"),
        "huggingface": ("HF_TOKEN", "https://router.huggingface.co/v1", "YUKI_HF_MODEL"),
        "zai": ("ZAI_API_KEY", "https://api.z.ai/api/paas/v4", "YUKI_ZAI_MODEL"),
    }
    if selected in compatible:
        key_env, base_url, model_env = compatible[selected]
        model = str(env.get(model_env, "")).strip()
        if not model:
            raise ValueError(f"Set {model_env} to the exact model identifier.")
        return OpenAICompatibleProvider(
            model, api_key_env=key_env, base_url=str(env.get(f"{model_env}_BASE_URL", base_url)),
            timeout=float(env.get("YUKI_PROVIDER_TIMEOUT_SECONDS", 45)), environ=env,
        )
    if selected not in {"ollama", "hybrid"}:
        raise ValueError("provider must be one of: mock, ollama, gemini, hybrid, openai, deepseek, kimi, minimax, openrouter, tokenrouter, huggingface, zai.")
    local = OllamaProvider(
        base_url=str(cfg.get("ollama_base_url", "http://127.0.0.1:11434")),
        model=str(cfg.get("ollama_model", env.get("YUKI_OLLAMA_MODEL", ""))),
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
            timeout=float(env.get("YUKI_PROVIDER_TIMEOUT_SECONDS", 45)), environ=env,
        )
    raw_tasks = str(env.get("YUKI_CLOUD_TASKS", "")).strip()
    cloud_tasks = [item.strip() for item in raw_tasks.split(",") if item.strip()]
    return HybridProvider(local, cloud, cloud_tasks=cloud_tasks)
