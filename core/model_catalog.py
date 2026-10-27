"""Shared model/provider catalog for CLI and desktop onboarding.

Curated presets are suggestions, not a promise of free availability. User-entered
model IDs remain supported so the catalog does not freeze the ecosystem.
"""
from __future__ import annotations

from typing import Any

MODEL_CATALOG: dict[str, dict[str, Any]] = {
    "ollama": {
        "kind": "local", "label": "Ollama", "base_url": "http://127.0.0.1:11434",
        "key_env": "", "models": [
            {"id": "qwen2.5:7b", "label": "Qwen 2.5 7B", "note": "General-purpose local model"},
            {"id": "llama3.2:3b", "label": "Llama 3.2 3B", "note": "Smaller local model"},
            {"id": "gemma3:4b", "label": "Gemma 3 4B", "note": "Compact multimodal-capable family"},
            {"id": "deepseek-r1:8b", "label": "DeepSeek R1 8B", "note": "Reasoning-focused local model"},
        ],
    },
    "lm-studio": {
        "kind": "local", "label": "LM Studio", "base_url": "http://127.0.0.1:1234/v1",
        "key_env": "", "models": [],
    },
    "llama.cpp": {
        "kind": "local", "label": "llama.cpp server", "base_url": "http://127.0.0.1:8080/v1",
        "key_env": "", "models": [],
    },
    "openai": {
        "kind": "cloud", "label": "OpenAI API", "base_url": "https://api.openai.com/v1",
        "key_env": "OPENAI_API_KEY", "models": [
            {"id": "gpt-4.1-mini", "label": "GPT-4.1 mini", "note": "Check current API pricing and availability"},
            {"id": "gpt-4.1-nano", "label": "GPT-4.1 nano", "note": "Check current API pricing and availability"},
        ],
    },
    "gemini_api": {
        "kind": "cloud", "label": "Google Gemini API",
        "base_url": "https://generativelanguage.googleapis.com/v1beta",
        "key_env": "GEMINI_API_KEY", "models": [
            {"id": "gemini-2.5-flash", "label": "Gemini 2.5 Flash", "note": "Free-tier access subject to current Google limits"},
            {"id": "gemini-2.5-flash-lite", "label": "Gemini 2.5 Flash-Lite", "note": "Check current free-tier eligibility"},
        ],
    },
    "nvidia": {
        "kind": "cloud", "label": "NVIDIA NIM", "base_url": "https://integrate.api.nvidia.com/v1",
        "key_env": "NVIDIA_API_KEY", "models": [
            {"id": "openai/gpt-oss-20b", "label": "GPT OSS 20B", "note": "Availability and rate limits depend on NVIDIA account"},
            {"id": "meta/llama-3.3-70b-instruct", "label": "Llama 3.3 70B Instruct", "note": "Check current endpoint availability"},
        ],
    },
    "deepseek": {
        "kind": "cloud", "label": "DeepSeek API", "base_url": "https://api.deepseek.com/v1",
        "key_env": "DEEPSEEK_API_KEY", "models": [
            {"id": "deepseek-chat", "label": "DeepSeek Chat", "note": "Check current pricing and availability"},
            {"id": "deepseek-reasoner", "label": "DeepSeek Reasoner", "note": "Check current pricing and availability"},
        ],
    },
    "anthropic": {
        "kind": "cloud", "label": "Anthropic API", "base_url": "https://api.anthropic.com",
        "key_env": "ANTHROPIC_API_KEY", "models": [
            {"id": "claude-3-5-haiku-latest", "label": "Claude Haiku", "note": "Use an API model ID enabled for your account"},
        ],
    },
    "openai-compatible": {
        "kind": "cloud", "label": "Custom OpenAI-compatible endpoint",
        "base_url": "", "key_env": "YUKI_CUSTOM_API_KEY", "models": [],
    },
}


def list_providers(kind: str | None = None) -> list[dict[str, Any]]:
    """Return copy-safe provider metadata for onboarding/UI consumers."""
    if kind not in (None, "local", "cloud"):
        raise ValueError("kind must be local, cloud, or omitted.")
    result = []
    for provider_id, meta in MODEL_CATALOG.items():
        if kind is not None and meta["kind"] != kind:
            continue
        result.append({
            "id": provider_id,
            "label": meta["label"],
            "kind": meta["kind"],
            "base_url": meta["base_url"],
            "key_env": meta["key_env"],
            "models": [dict(model) for model in meta["models"]],
            "custom_model_allowed": True,
        })
    return result


def get_provider(provider_id: str) -> dict[str, Any]:
    if not isinstance(provider_id, str) or provider_id not in MODEL_CATALOG:
        raise ValueError("Unsupported model provider.")
    meta = MODEL_CATALOG[provider_id]
    return {
        "id": provider_id, "label": meta["label"], "kind": meta["kind"],
        "base_url": meta["base_url"], "key_env": meta["key_env"],
        "models": [dict(model) for model in meta["models"]],
        "custom_model_allowed": True,
    }
