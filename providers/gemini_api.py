"""Google Gemini API adapter with role-preserving structured chat."""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request

from providers.base import BaseProvider, validate_chat_messages

_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,119}$")
_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta"


class _RejectRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class GeminiAPIProvider(BaseProvider):
    """Gemini generateContent adapter; credentials are sent in a dedicated header."""

    def __init__(self, model: str, *, api_key_env="GEMINI_API_KEY",
                 timeout=45, environ=None):
        if not isinstance(model, str) or not _MODEL_RE.fullmatch(model.strip()):
            raise ValueError("Configure a valid Gemini model name.")
        if not isinstance(api_key_env, str) or not api_key_env.isidentifier():
            raise ValueError("API key environment variable name is invalid.")
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 1 <= timeout <= 120:
            raise ValueError("Timeout must be between 1 and 120 seconds.")
        self.model = model.strip()
        self.api_key_env = api_key_env
        self.timeout = float(timeout)
        self._environ = os.environ if environ is None else environ

    def is_available(self) -> bool:
        return bool(str(self._environ.get(self.api_key_env, "")).strip())

    def generate(self, prompt: str) -> str:
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 20_000:
            raise ValueError("Prompt must be non-empty text within the 20000-character limit.")
        return self._request({"contents": [{"role": "user", "parts": [{"text": prompt.strip()}]}]})

    def generate_chat(self, system: str, messages: list[dict]) -> str:
        validate_chat_messages(system, messages)
        contents = []
        for item in messages:
            contents.append({
                "role": "user" if item["role"] == "user" else "model",
                "parts": [{"text": item["content"]}],
            })
        return self._request({
            "systemInstruction": {"parts": [{"text": system.strip()}]},
            "contents": contents,
        })

    def _request(self, payload: dict) -> str:
        api_key = str(self._environ.get(self.api_key_env, "")).strip()
        if not api_key:
            raise RuntimeError(f"Gemini API is not configured; set {self.api_key_env}.")
        body = json.dumps(payload).encode("utf-8")
        url = f"{_ENDPOINT}/models/{self.model}:generateContent"
        request = urllib.request.Request(
            url, data=body,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "x-goog-api-key": api_key,
            },
            method="POST",
        )
        try:
            opener = urllib.request.build_opener(_RejectRedirects())
            with opener.open(request, timeout=self.timeout) as response:
                raw = response.read(2_000_001)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise RuntimeError("Gemini API request failed; redirects are not followed.") from exc
        if len(raw) > 2_000_000:
            raise RuntimeError("Gemini API response exceeded the 2 MB limit.")
        try:
            result = json.loads(raw.decode("utf-8"))
            parts = result["candidates"][0]["content"]["parts"]
            text = "".join(part["text"] for part in parts if isinstance(part, dict) and isinstance(part.get("text"), str))
        except (UnicodeDecodeError, json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
            raise RuntimeError("Gemini API returned an unsupported response.") from exc
        if not text.strip():
            raise RuntimeError("Gemini API returned no text response.")
        return text.strip()
