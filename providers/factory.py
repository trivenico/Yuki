"""Construct the configured provider without implicit cloud fallback."""
from __future__ import annotations

import os

from providers.gemini_cli import GeminiCLIProvider
from providers.anthropic import AnthropicProvider
from providers.hybrid import HybridProvider
from providers.ollama import OllamaProvider
from providers.openai_compatible import OpenAICompatibleProvider


# Explicit adapters for providers that expose the OpenAI Chat Completions API.
# Models are deliberately configured by the operator rather than guessed here.
_OPENAI_COMPATIBLE_PROVIDERS = {
    "deepseek": {
        "model_env": "YUKI_DEEPSEEK_MODEL",
        "model_config": "deepseek_model",
        "key_env": "DEEPSEEK_API_KEY",
        "base_env": "YUKI_DEEPSEEK_BASE_URL",
        "base_default": "https://api.deepseek.com/v1",
    },
    "kimi": {
        "model_env": "YUKI_KIMI_MODEL",
        "model_config": "kimi_model",
        "key_env": "MOONSHOT_API_KEY",
        "base_env": "YUKI_KIMI_BASE_URL",
        "base_default": "https://api.moonshot.ai/v1",
    },
    "minimax": {
        "model_env": "YUKI_MINIMAX_MODEL",
        "model_config": "minimax_model",
        "key_env": "MINIMAX_API_KEY",
        "base_env": "YUKI_MINIMAX_BASE_URL",
        "base_default": "https://api.minimax.chat/v1",
    },
    "openrouter": {
        "model_env": "YUKI_OPENROUTER_MODEL",
        "model_config": "openrouter_model",
        "key_env": "OPENROUTER_API_KEY",
        "base_env": "YUKI_OPENROUTER_BASE_URL",
        "base_default": "https://openrouter.ai/api/v1",
    },
    "tokenrouter": {
        "model_env": "YUKI_TOKENROUTER_MODEL",
        "model_config": "tokenrouter_model",
        "key_env": "TOKENROUTER_API_KEY",
        "base_env": "YUKI_TOKENROUTER_BASE_URL",
        "base_default": "https://api.tokenrouter.io/v1",
    },
}


def create_provider(config=None, *, environ=None):
    """Build the configured real provider without implicit fallback.

    Ollama is the default so prompts stay local unless a cloud provider is explicitly selected. Hybrid routing keeps general tasks local and only
    routes explicitly allow-listed task names to cloud. API keys are read from
    the process environment, never from YAML or committed files. Mock providers
    are rejected by this runtime factory and must be injected directly by tests.
    """
    cfg = dict(config or {})
    env = os.environ if environ is None else environ
    # Environment selection is the operator override and must win over YAML/config.
    selected = str(env.get("YUKI_PROVIDER", cfg.get("provider", "ollama"))).strip().lower()
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
    if selected == "anthropic":
        model = str(env.get("YUKI_ANTHROPIC_MODEL", cfg.get("anthropic_model", ""))).strip()
        if not model:
            raise ValueError(
                "Configure a model with YUKI_ANTHROPIC_MODEL or anthropic_model before selecting anthropic."
            )
        return AnthropicProvider(
            model,
            base_url=str(env.get("YUKI_ANTHROPIC_BASE_URL", "https://api.anthropic.com/v1")),
            environ=env,
        )
    if selected in _OPENAI_COMPATIBLE_PROVIDERS:
        settings = _OPENAI_COMPATIBLE_PROVIDERS[selected]
        model = str(env.get(settings["model_env"], cfg.get(settings["model_config"], ""))).strip()
        if not model:
            raise ValueError(
                f"Configure a model with {settings['model_env']} or "
                f"{settings['model_config']} before selecting {selected}."
            )
        return OpenAICompatibleProvider(
            model,
            api_key_env=settings["key_env"],
            base_url=str(env.get(settings["base_env"], settings["base_default"])),
            environ=env,
        )
    if selected not in {"ollama", "hybrid"}:
        raise ValueError("provider must be one of: openai, ollama, gemini, anthropic, hybrid, deepseek, kimi, minimax, openrouter, tokenrouter (mock is test-only).")
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
