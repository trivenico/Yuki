"""Truthful provider/model catalog for Yuki onboarding and settings.

The catalog distinguishes implemented runtime adapters from future integrations.
Model names are suggestions; operators may enter a model ID supported by their account.
"""
from __future__ import annotations

from typing import Any

MODEL_CATALOG: dict[str, dict[str, Any]] = {
    "ollama": {
        "kind": "local", "label": "Ollama", "runtime_supported": True,
        "key_env": "", "model_config": "ollama_model",
        "models": [
            {"id": "llama3.2:3b", "label": "Llama 3.2 3B", "note": "Small local default"},
            {"id": "qwen2.5:7b", "label": "Qwen 2.5 7B", "note": "General-purpose local model"},
            {"id": "gemma3:4b", "label": "Gemma 3 4B", "note": "Compact multimodal model family"},
            {"id": "deepseek-r1:8b", "label": "DeepSeek R1 8B", "note": "Reasoning-focused local model"},
        ],
    },
    "openai": {
        "kind": "cloud", "label": "OpenAI API", "runtime_supported": True,
        "key_env": "OPENAI_API_KEY", "model_config": "openai_model",
        "models": [
            {"id": "gpt-4.1-mini", "label": "GPT-4.1 mini", "note": "Check current API pricing and availability"},
            {"id": "gpt-4.1-nano", "label": "GPT-4.1 nano", "note": "Check current API pricing and availability"},
        ],
    },
    "gemini": {
        "kind": "cloud", "label": "Gemini CLI", "runtime_supported": True,
        "key_env": "", "model_config": None,
        "models": [],
    },
    "anthropic": {
        "kind": "cloud", "label": "Anthropic API", "runtime_supported": True,
        "key_env": "ANTHROPIC_API_KEY", "model_config": "anthropic_model",
        "models": [],
    },
    "deepseek": {
        "kind": "cloud", "label": "DeepSeek API", "runtime_supported": True,
        "key_env": "DEEPSEEK_API_KEY", "model_config": "deepseek_model",
        "models": [{"id": "deepseek-chat", "label": "DeepSeek Chat", "note": "Check current pricing and availability"}],
    },
    "kimi": {
        "kind": "cloud", "label": "Kimi API", "runtime_supported": True,
        "key_env": "MOONSHOT_API_KEY", "model_config": "kimi_model",
        "models": [],
    },
    "minimax": {
        "kind": "cloud", "label": "MiniMax API", "runtime_supported": True,
        "key_env": "MINIMAX_API_KEY", "model_config": "minimax_model",
        "models": [],
    },
    "openrouter": {
        "kind": "cloud", "label": "OpenRouter", "runtime_supported": True,
        "key_env": "OPENROUTER_API_KEY", "model_config": "openrouter_model",
        "models": [],
    },
    "tokenrouter": {
        "kind": "cloud", "label": "TokenRouter", "runtime_supported": True,
        "key_env": "TOKENROUTER_API_KEY", "model_config": "tokenrouter_model",
        "models": [],
    },
    "hybrid": {
        "kind": "hybrid", "label": "Hybrid (local + explicitly allowed cloud tasks)",
        "runtime_supported": True, "key_env": "", "model_config": None, "models": [],
    },
    "gemini_api": {
        "kind": "cloud", "label": "Google Gemini API", "runtime_supported": False,
        "key_env": "GEMINI_API_KEY", "model_config": None, "models": [],
    },
    "nvidia": {
        "kind": "cloud", "label": "NVIDIA NIM", "runtime_supported": False,
        "key_env": "NVIDIA_API_KEY", "model_config": None, "models": [],
    },
    "lm-studio": {
        "kind": "local", "label": "LM Studio", "runtime_supported": False,
        "key_env": "", "model_config": None, "models": [],
    },
    "llama.cpp": {
        "kind": "local", "label": "llama.cpp server", "runtime_supported": False,
        "key_env": "", "model_config": None, "models": [],
    },
}


def list_providers(kind: str | None = None, *, supported_only: bool = False) -> list[dict[str, Any]]:
    """Return independent provider metadata; optional kind filters are validated."""
    if kind not in (None, "local", "cloud", "hybrid"):
        raise ValueError("kind must be local, cloud, hybrid, or omitted.")
    result = []
    for provider_id, meta in MODEL_CATALOG.items():
        if kind is not None and meta["kind"] != kind:
            continue
        if supported_only and not meta["runtime_supported"]:
            continue
        result.append({
            "id": provider_id, "label": meta["label"], "kind": meta["kind"],
            "key_env": meta["key_env"], "model_config": meta["model_config"],
            "models": [dict(model) for model in meta["models"]],
            "custom_model_allowed": True,
            "runtime_supported": bool(meta["runtime_supported"]),
        })
    return result


def get_provider(provider_id: str) -> dict[str, Any]:
    if not isinstance(provider_id, str) or provider_id not in MODEL_CATALOG:
        raise ValueError("Unsupported model provider.")
    meta = MODEL_CATALOG[provider_id]
    return {
        "id": provider_id, "label": meta["label"], "kind": meta["kind"],
        "key_env": meta["key_env"], "model_config": meta["model_config"],
        "models": [dict(model) for model in meta["models"]],
        "custom_model_allowed": True,
        "runtime_supported": bool(meta["runtime_supported"]),
    }
