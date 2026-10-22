"""Sequential failover across OpenAI-compatible model endpoints.

Fail over only on rate limits, capacity/server errors, and transient network
failures. Authentication, invalid requests, and policy errors are not retried.
"""
from __future__ import annotations

from providers.openai_compatible import OpenAICompatibleProvider, RetryableProviderError


class FailoverProvider:
    def __init__(self, models, *, api_key_env="OPENROUTER_API_KEY",
                 base_url="https://openrouter.ai/api/v1", timeout=45,
                 provider_factory=OpenAICompatibleProvider):
        if isinstance(models, str) or not isinstance(models, (list, tuple)) or not models:
            raise ValueError("At least one failover model is required.")
        normalized = []
        seen = set()
        for model in models:
            if not isinstance(model, str) or not model.strip() or len(model.strip()) > 120:
                raise ValueError("Failover model names must be non-empty strings of at most 120 characters.")
            key = model.strip()
            if key not in seen:
                normalized.append(key)
                seen.add(key)
        self.models = tuple(normalized)
        self.providers = tuple(provider_factory(
            model, api_key_env=api_key_env, base_url=base_url, timeout=timeout
        ) for model in self.models)
        self.last_provider = None
        self.last_model = None
        self.last_error = None

    def is_available(self):
        return any(provider.is_available() for provider in self.providers)

    @property
    def last_context_budget(self):
        budget = getattr(self.last_provider, "last_context_budget", None)
        return budget if isinstance(budget, dict) else None

    def generate(self, prompt: str) -> str:
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("Prompt must be non-empty text.")
        failures = []
        for provider in self.providers:
            try:
                result = provider.generate(prompt)
            except RetryableProviderError as exc:
                failures.append(f"{provider.model}: {exc}")
                self.last_error = str(exc)
                continue
            self.last_provider = provider
            self.last_model = provider.model
            self.last_error = None
            return result
        raise RuntimeError(
            "All configured fallback models are temporarily unavailable or rate-limited; "
            "no response was produced. " + "; ".join(failures)
        )
