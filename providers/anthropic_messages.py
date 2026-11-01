"""Anthropic Messages API adapter for Claude Fable and approved Mythos models.

Text-only first integration. It never executes model-proposed tools; Yuki's
separately authorized worker/runtime remains the only execution boundary.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from providers.openai_compatible import _RejectRedirects, _trusted_https_endpoint

_ALLOWED_EFFORTS = frozenset({"low", "medium", "high", "xhigh", "max"})


class AnthropicMessagesProvider:
    """Bounded Claude Messages API client with adaptive model reasoning."""

    def __init__(
        self,
        model: str,
        *,
        api_key_env: str = "ANTHROPIC_API_KEY",
        base_url: str = "https://api.anthropic.com",
        timeout: int | float = 120,
        effort: str = "high",
        max_output_tokens: int = 8192,
        environ=None,
    ):
        import os

        if not isinstance(model, str) or not model.strip() or len(model) > 120:
            raise ValueError("Configure a valid Anthropic model ID.")
        if not isinstance(api_key_env, str) or not api_key_env.isidentifier():
            raise ValueError("API key environment variable name is invalid.")
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 1 <= timeout <= 180:
            raise ValueError("Timeout must be between 1 and 180 seconds.")
        if effort not in _ALLOWED_EFFORTS:
            raise ValueError("effort must be low, medium, high, xhigh, or max.")
        if isinstance(max_output_tokens, bool) or not isinstance(max_output_tokens, int) or not 1 <= max_output_tokens <= 128000:
            raise ValueError("max_output_tokens must be between 1 and 128000.")
        trusted_url = _trusted_https_endpoint(base_url, {"api.anthropic.com"})
        parsed_endpoint = urlsplit(trusted_url)
        if (parsed_endpoint.hostname != "api.anthropic.com"
                or parsed_endpoint.port not in (None, 443)
                or parsed_endpoint.path):
            raise ValueError("Anthropic credentials may only be sent to https://api.anthropic.com.")
        self.base_url = trusted_url
        self.model = model.strip()
        self.api_key_env = api_key_env
        self.timeout = timeout
        self.effort = effort
        self.max_output_tokens = max_output_tokens
        self._environ = os.environ if environ is None else environ

    def is_available(self) -> bool:
        return bool(self._environ.get(self.api_key_env, "").strip())

    def generate(self, prompt: str) -> str:
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("Prompt must be non-empty text.")
        api_key = self._environ.get(self.api_key_env, "").strip()
        if not api_key:
            raise RuntimeError(f"Anthropic is not configured; set {self.api_key_env}.")
        payload = json.dumps({
            "model": self.model,
            "max_tokens": self.max_output_tokens,
            "output_config": {"effort": self.effort},
            "messages": [{"role": "user", "content": prompt.strip()[:100000]}],
        }, allow_nan=False).encode("utf-8")
        request = urllib.request.Request(
            self.base_url + "/v1/messages",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
            },
            method="POST",
        )
        try:
            opener = urllib.request.build_opener(_RejectRedirects())
            with opener.open(request, timeout=self.timeout) as response:
                raw = response.read(2_000_001)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise RuntimeError("Anthropic Messages API request failed; redirects are not followed.") from exc
        if len(raw) > 2_000_000:
            raise RuntimeError("Anthropic response exceeded the 2 MB limit.")
        try:
            body = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("Anthropic returned invalid JSON.") from exc
        if not isinstance(body, dict) or body.get("type") != "message":
            raise RuntimeError("Anthropic returned an unsupported or incomplete response.")
        chunks = [
            item["text"] for item in body.get("content", [])
            if isinstance(item, dict) and item.get("type") == "text"
            and isinstance(item.get("text"), str) and item["text"]
        ]
        result = "".join(chunks).strip()
        if not result:
            raise RuntimeError("Anthropic returned no text output.")
        return result
