"""Native Anthropic Messages API adapter; credentials are environment-only."""
from __future__ import annotations
import json
import os
import urllib.error
import urllib.request


class _RejectRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class AnthropicProvider:
    """Minimal text-only Messages API adapter with a fixed trusted endpoint."""
    def __init__(self, model, *, api_key_env="ANTHROPIC_API_KEY", timeout=45, environ=None):
        if not isinstance(model, str) or not model.strip() or len(model) > 120:
            raise ValueError("Configure a valid Anthropic model name.")
        if not isinstance(api_key_env, str) or not api_key_env.isidentifier():
            raise ValueError("API key environment variable name is invalid.")
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 1 <= timeout <= 120:
            raise ValueError("Timeout must be between 1 and 120 seconds.")
        self.model, self.api_key_env, self.timeout = model.strip(), api_key_env, timeout
        self._environ = os.environ if environ is None else environ

    def is_available(self):
        return bool(self._environ.get(self.api_key_env, "").strip())

    def generate(self, prompt):
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("Prompt must be non-empty text.")
        key = self._environ.get(self.api_key_env, "").strip()
        if not key:
            raise RuntimeError(f"Anthropic provider is not configured; set {self.api_key_env}.")
        payload = json.dumps({
            "model": self.model, "max_tokens": 4096,
            "messages": [{"role": "user", "content": prompt.strip()[:20000]}],
        }).encode("utf-8")
        request = urllib.request.Request(
            "https://api.anthropic.com/v1/messages", data=payload,
            headers={"content-type": "application/json", "x-api-key": key,
                     "anthropic-version": "2023-06-01"}, method="POST",
        )
        try:
            with urllib.request.build_opener(_RejectRedirects()).open(request, timeout=self.timeout) as response:
                raw = response.read(2_000_001)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise RuntimeError("Anthropic request failed; redirects are not followed.") from exc
        if len(raw) > 2_000_000:
            raise RuntimeError("Anthropic response exceeded the 2 MB limit.")
        try:
            body = json.loads(raw.decode("utf-8"))
            answer = "\\n".join(
                item["text"] for item in body["content"]
                if isinstance(item, dict) and item.get("type") == "text"
                and isinstance(item.get("text"), str)
            )
        except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError) as exc:
            raise RuntimeError("Anthropic returned an unsupported response.") from exc
        if not answer.strip():
            raise RuntimeError("Anthropic returned no text response.")
        return answer.strip()
