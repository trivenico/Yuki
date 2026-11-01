"""Construct configured providers without implicit cloud fallback."""
from __future__ import annotations
import os
from providers.gemini_cli import GeminiCLIProvider
from providers.hybrid import HybridProvider
from providers.mock import MockProvider
from providers.ollama import OllamaProvider
from providers.openai_compatible import OpenAICompatibleProvider
from providers.anthropic_messages import AnthropicMessagesProvider
from providers.openai_responses import OpenAIResponsesProvider

def create_provider(config=None, *, environ=None):
    """Build selected provider; credentials come only from environment."""
    cfg = dict(config or {})
    env = os.environ if environ is None else environ
    selected = str(cfg.get("provider", "openai")).strip().lower()
    if selected == "mock":
        return MockProvider()
    if selected == "openai":
        model = str(env.get("YUKI_OPENAI_MODEL", cfg.get("openai_model", "gpt-4.1-mini"))).strip()
        return OpenAICompatibleProvider(model, api_key_env="OPENAI_API_KEY",
            base_url=str(env.get("YUKI_OPENAI_BASE_URL", "https://api.openai.com/v1")), environ=env)
    if selected == "openai_responses":
        model = str(env.get("YUKI_OPENAI_RESPONSES_MODEL", cfg.get("openai_responses_model", ""))).strip()
        if not model:
            raise ValueError("Set YUKI_OPENAI_RESPONSES_MODEL to a model ID available to your OpenAI project.")
        return OpenAIResponsesProvider(model, api_key_env="OPENAI_API_KEY",
            base_url=str(env.get("YUKI_OPENAI_BASE_URL", "https://api.openai.com/v1")),
            timeout=float(env.get("YUKI_PROVIDER_TIMEOUT_SECONDS", 90)),
            reasoning_effort=str(env.get("YUKI_OPENAI_REASONING_EFFORT", cfg.get("openai_reasoning_effort", "medium"))),
            max_output_tokens=int(env.get("YUKI_OPENAI_MAX_OUTPUT_TOKENS", cfg.get("openai_max_output_tokens", 8192))),
            environ=env)
    if selected == "anthropic":
        model = str(env.get("YUKI_ANTHROPIC_MODEL", cfg.get("anthropic_model", ""))).strip()
        if not model:
            raise ValueError("Set YUKI_ANTHROPIC_MODEL to a model ID available to your Anthropic account.")
        return AnthropicMessagesProvider(
            model, api_key_env="ANTHROPIC_API_KEY",
            base_url=str(env.get("YUKI_ANTHROPIC_BASE_URL", "https://api.anthropic.com")),
            timeout=float(env.get("YUKI_PROVIDER_TIMEOUT_SECONDS", 120)),
            effort=str(env.get("YUKI_ANTHROPIC_EFFORT", cfg.get("anthropic_effort", "high"))),
            max_output_tokens=int(env.get("YUKI_ANTHROPIC_MAX_OUTPUT_TOKENS", cfg.get("anthropic_max_output_tokens", 8192))),
            environ=env,
        )
    if selected == "gemini":
        return GeminiCLIProvider(timeout=float(env.get("YUKI_PROVIDER_TIMEOUT_SECONDS", 45)),
            max_prompt_chars=cfg.get("gemini_max_prompt_chars", 20000),
            max_output_chars=cfg.get("gemini_max_output_chars", 50000))
    if selected not in {"ollama", "hybrid"}:
        raise ValueError("provider must be one of: openai, openai_responses, anthropic, ollama, gemini, hybrid (mock is test-only).")
    local = OllamaProvider(base_url=str(cfg.get("ollama_base_url", "http://127.0.0.1:11434")),
        model=str(cfg.get("ollama_model", env.get("YUKI_OLLAMA_MODEL", ""))))
    if selected == "ollama":
        return local
    cloud_model = str(env.get("YUKI_CLOUD_MODEL", "")).strip()
    cloud = OpenAICompatibleProvider(cloud_model, api_key_env=str(env.get("YUKI_CLOUD_API_KEY_ENV", "OPENAI_API_KEY")),
        base_url=str(env.get("YUKI_CLOUD_BASE_URL", "https://api.openai.com/v1"))) if cloud_model else None
    tasks = [item.strip() for item in str(env.get("YUKI_CLOUD_TASKS", "")).split(",") if item.strip()]
    return HybridProvider(local, cloud, cloud_tasks=tasks)
