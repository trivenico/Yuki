"""OpenAI-compatible cloud chat adapter; credentials are read from the environment."""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from core.context_budget import context_budget


class RetryableProviderError(RuntimeError):
    """A provider failure that is safe to retry on another configured model."""


class OpenAICompatibleProvider:
    """A small Chat Completions adapter. No API key is stored in source/config files."""

    def __init__(self, model, *, api_key_env="OPENAI_API_KEY",
                 base_url="https://api.openai.com/v1", timeout=45, usage_tracker=None, context_limit_tokens=None):
        if not isinstance(model, str) or not model.strip() or len(model) > 120:
            raise ValueError("Configure a valid cloud model name.")
        if not isinstance(api_key_env, str) or not api_key_env.isidentifier():
            raise ValueError("API key environment variable name is invalid.")
        parsed = urlsplit(base_url.strip().rstrip("/")) if isinstance(base_url, str) else None
        if (parsed is None or parsed.scheme != "https" or not parsed.hostname
                or parsed.username or parsed.password or parsed.query or parsed.fragment):
            raise ValueError("Cloud provider endpoint must be an HTTPS URL without credentials.")
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 1 <= timeout <= 120:
            raise ValueError("Timeout must be between 1 and 120 seconds.")
        self.model = model.strip()
        self.api_key_env = api_key_env
        self.base_url = base_url.strip().rstrip("/")
        self.timeout = timeout
        self.usage_tracker = usage_tracker
        self.context_limit_tokens = self._validate_context_limit(context_limit_tokens)
        self.last_context_budget = context_budget(limit_tokens=self.context_limit_tokens, used_tokens=None)

    @staticmethod
    def _validate_context_limit(value):
        if value is None:
            return None
        if type(value) is not int or value <= 0:
            raise ValueError("context_limit_tokens must be a positive integer or None.")
        return value

    def is_available(self):
        return bool(os.environ.get(self.api_key_env, "").strip())

    def generate(self, prompt: str) -> str:
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("Prompt must be non-empty text.")
        api_key = os.environ.get(self.api_key_env, "").strip()
        if not api_key:
            raise RuntimeError(f"Cloud provider is not configured; set {self.api_key_env}.")
        payload = json.dumps({
            "model": self.model,
            "messages": [{"role": "user", "content": prompt.strip()[:20000]}],
            "stream": False,
        }).encode("utf-8")
        request = urllib.request.Request(
            self.base_url + "/chat/completions",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Authorization": "Bearer " + api_key,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read(2_000_001)
        except urllib.error.HTTPError as exc:
            if exc.code == 429 or 500 <= exc.code <= 599:
                raise RetryableProviderError(f"Provider returned retryable HTTP status {exc.code}.") from exc
            raise RuntimeError(f"Provider rejected the request with HTTP status {exc.code}.") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise RetryableProviderError("Provider network request failed.") from exc
        if len(raw) > 2_000_000:
            raise RuntimeError("Cloud provider response exceeded the 2 MB limit.")
        try:
            body = json.loads(raw.decode("utf-8"))
            text = body["choices"][0]["message"]["content"]
        except (UnicodeDecodeError, json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
            raise RuntimeError("Cloud provider returned an unsupported response.") from exc
        if not isinstance(text, str) or not text.strip():
            raise RuntimeError("Cloud provider returned no text response.")
        usage = body.get("usage") if isinstance(body, dict) else None
        prompt_tokens = usage.get("prompt_tokens") if isinstance(usage, dict) else None
        output_tokens = usage.get("completion_tokens") if isinstance(usage, dict) else None
        exact_usage = (type(prompt_tokens) is int and prompt_tokens >= 0
                       and type(output_tokens) is int and output_tokens >= 0)
        if not exact_usage:
            prompt_tokens = max(1, len(prompt.strip()) // 4)
            output_tokens = max(1, len(text) // 4)
        self.last_context_budget = context_budget(
            limit_tokens=self.context_limit_tokens,
            used_tokens=prompt_tokens + output_tokens,
            estimated=not exact_usage,
        )
        if self.usage_tracker is not None:
            self.usage_tracker.record(
                provider="openai-compatible", model=self.model,
                input_tokens=prompt_tokens, output_tokens=output_tokens,
                estimated=not exact_usage,
            )
        return text.strip()
