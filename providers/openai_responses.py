"""OpenAI Responses API adapter for reasoning-capable models such as GPT-6 Astra.

This adapter intentionally exposes text generation only. Tool execution remains
owned by Yuki's separately authorized worker/runtime, never by model output.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

from providers.openai_compatible import _RejectRedirects, _trusted_https_endpoint


_ALLOWED_EFFORTS = frozenset({"low", "medium", "high", "xhigh", "max"})


class OpenAIResponsesProvider:
    """Minimal, bounded Responses API client using environment-only credentials."""

    def __init__(
        self,
        model: str,
        *,
        api_key_env: str = "OPENAI_API_KEY",
        base_url: str = "https://api.openai.com/v1",
        timeout: int | float = 90,
        reasoning_effort: str = "medium",
        max_output_tokens: int = 8192,
        allowed_hosts=None,
        environ=None,
    ):
        import os

        if not isinstance(model, str) or not model.strip() or len(model) > 120:
            raise ValueError("Configure a valid OpenAI model name.")
        if not isinstance(api_key_env, str) or not api_key_env.isidentifier():
            raise ValueError("API key environment variable name is invalid.")
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 1 <= timeout <= 180:
            raise ValueError("Timeout must be between 1 and 180 seconds.")
        if reasoning_effort not in _ALLOWED_EFFORTS:
            raise ValueError("reasoning_effort must be low, medium, high, xhigh, or max.")
        if isinstance(max_output_tokens, bool) or not isinstance(max_output_tokens, int) or not 1 <= max_output_tokens <= 128000:
            raise ValueError("max_output_tokens must be between 1 and 128000.")
        self.base_url = _trusted_https_endpoint(base_url, allowed_hosts)
        self.model = model.strip()
        self.api_key_env = api_key_env
        self.timeout = timeout
        self.reasoning_effort = reasoning_effort
        self.max_output_tokens = max_output_tokens
        self._environ = os.environ if environ is None else environ

    def is_available(self) -> bool:
        return bool(self._environ.get(self.api_key_env, "").strip())

    def generate(self, prompt: str) -> str:
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("Prompt must be non-empty text.")
        api_key = self._environ.get(self.api_key_env, "").strip()
        if not api_key:
            raise RuntimeError(f"OpenAI is not configured; set {self.api_key_env}.")
        payload = json.dumps({
            "model": self.model,
            "input": prompt.strip()[:100000],
            "reasoning": {"effort": self.reasoning_effort},
            "max_output_tokens": self.max_output_tokens,
        }, allow_nan=False).encode("utf-8")
        request = urllib.request.Request(
            self.base_url + "/responses",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Authorization": "Bearer " + api_key,
            },
            method="POST",
        )
        try:
            opener = urllib.request.build_opener(_RejectRedirects())
            with opener.open(request, timeout=self.timeout) as response:
                raw = response.read(2_000_001)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            # Do not expose request headers, response bodies, or credential values.
            raise RuntimeError("OpenAI Responses API request failed; redirects are not followed.") from exc
        if len(raw) > 2_000_000:
            raise RuntimeError("OpenAI Responses API response exceeded the 2 MB limit.")
        try:
            body = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("OpenAI Responses API returned invalid JSON.") from exc
        if not isinstance(body, dict) or body.get("status") not in {"completed", None}:
            raise RuntimeError("OpenAI Responses API did not complete the response.")
        chunks = []
        for item in body.get("output", []):
            if not isinstance(item, dict) or item.get("type") != "message":
                continue
            for content in item.get("content", []):
                if isinstance(content, dict) and content.get("type") == "output_text":
                    value = content.get("text")
                    if isinstance(value, str) and value:
                        chunks.append(value)
        text = "".join(chunks).strip()
        if not text:
            raise RuntimeError("OpenAI Responses API returned no text output.")
        return text
