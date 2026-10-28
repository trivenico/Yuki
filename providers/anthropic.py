"""Native Anthropic Messages API adapter with bounded responses and trusted HTTPS."""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from providers.openai_compatible import _RejectRedirects, _trusted_https_endpoint


class AnthropicProvider:
    """Anthropic Messages API adapter; credentials are read from environment only."""

    def __init__(
        self,
        model: str,
        *,
        api_key_env: str = "ANTHROPIC_API_KEY",
        base_url: str = "https://api.anthropic.com/v1",
        timeout: float = 45,
        environ=None,
    ):
        if not isinstance(model, str) or not model.strip() or len(model) > 120:
            raise ValueError("Configure a valid Anthropic model name.")
        if not isinstance(api_key_env, str) or not api_key_env.isidentifier():
            raise ValueError("API key environment variable name is invalid.")
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 1 <= timeout <= 120:
            raise ValueError("Timeout must be between 1 and 120 seconds.")
        self.base_url = _trusted_https_endpoint(base_url)
        self.model = model.strip()
        self.api_key_env = api_key_env
        self.timeout = timeout
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
            "max_tokens": 2048,
            "messages": [{"role": "user", "content": prompt.strip()[:20000]}],
        }).encode("utf-8")
        request = urllib.request.Request(
            self.base_url + "/messages",
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
            raise RuntimeError("Anthropic request failed; redirects are not followed.") from exc
        if len(raw) > 2_000_000:
            raise RuntimeError("Anthropic response exceeded the 2 MB limit.")
        try:
            body = json.loads(raw.decode("utf-8"))
            blocks = body["content"]
            text = "\n".join(
                block["text"] for block in blocks
                if isinstance(block, dict) and block.get("type") == "text"
                and isinstance(block.get("text"), str)
            )
        except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError) as exc:
            raise RuntimeError("Anthropic returned an unsupported response.") from exc
        if not text.strip():
            raise RuntimeError("Anthropic returned no text response.")
        return text.strip()
