"""Ollama bridge for Yuki's provider-neutral evaluation engine.

This adapter uses an already configured OllamaProvider. It never downloads models,
reads credentials, follows a cloud fallback, or enables tool execution.
"""
from __future__ import annotations

import re
from typing import Any

from providers.ollama import OllamaProvider

_MODEL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/@+-]{0,159}$")
_SHA256 = re.compile(r"^[a-fA-F0-9]{64}$")


class OllamaEvaluationAdapter:
    """Callable generate(prompt) adapter accepted by evaluate_candidate()."""

    provider_id = "ollama"

    def __init__(
        self,
        provider: OllamaProvider,
        *,
        runtime_id: str = "ollama-local",
        artifact_sha256: str | None = None,
    ):
        if not isinstance(provider, OllamaProvider):
            raise TypeError("provider_must_be_OllamaProvider")
        if not isinstance(runtime_id, str) or not _MODEL_ID.fullmatch(runtime_id):
            raise ValueError("invalid_runtime_id")
        if artifact_sha256 is not None and (
            not isinstance(artifact_sha256, str) or not _SHA256.fullmatch(artifact_sha256)
        ):
            raise ValueError("invalid_artifact_sha256")
        self.provider = provider
        self.model_id = provider.model
        self.runtime_id = runtime_id
        self.artifact_sha256 = artifact_sha256.lower() if artifact_sha256 else None

    def __call__(self, prompt: str) -> str:
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 8_000:
            raise ValueError("prompt_must_be_non_empty_and_at_most_8000_characters")
        result = self.provider.generate_chat(
            messages=[{"role": "user", "content": prompt}],
            tools=[],
            tool_choice="none",
        )
        if not isinstance(result, dict):
            raise RuntimeError("ollama_evaluation_response_invalid")
        text = result.get("text")
        tool_calls = result.get("tool_calls")
        if not isinstance(text, str) or not text.strip():
            raise RuntimeError("ollama_evaluation_response_missing_text")
        # Evaluation is inference-only. Tool proposals are never executed or scored.
        if tool_calls != {"tool_calls": []}:
            raise RuntimeError("ollama_evaluation_tool_calls_not_permitted")
        return text[:20_000]
