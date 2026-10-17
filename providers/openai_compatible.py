"""OpenAI-compatible cloud chat adapter; credentials are read from the environment."""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from urllib.parse import urlsplit


class OpenAICompatibleProvider:
    """A small Chat Completions adapter. No API key is stored in source/config files."""

    def __init__(self, model, *, api_key_env="OPENAI_API_KEY",
                 base_url="https://api.openai.com/v1", timeout=45):
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
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise RuntimeError("Cloud provider request failed.") from exc
        if len(raw) > 2_000_000:
            raise RuntimeError("Cloud provider response exceeded the 2 MB limit.")
        try:
            body = json.loads(raw.decode("utf-8"))
            text = body["choices"][0]["message"]["content"]
        except (UnicodeDecodeError, json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
            raise RuntimeError("Cloud provider returned an unsupported response.") from exc
        if not isinstance(text, str) or not text.strip():
            raise RuntimeError("Cloud provider returned no text response.")
        return text.strip()
