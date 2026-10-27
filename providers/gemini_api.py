"""Gemini Developer API provider using the standard library."""
from __future__ import annotations
import json
import os
import urllib.error
import urllib.request
from urllib.parse import quote

class _RejectRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class GeminiAPIProvider:
    def __init__(self, model="gemini-2.5-flash", *, api_key_env="GEMINI_API_KEY", timeout=45, environ=None):
        if not isinstance(model, str) or not model.strip() or len(model) > 120:
            raise ValueError("Configure a valid Gemini model name.")
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 1 <= timeout <= 120:
            raise ValueError("Timeout must be between 1 and 120 seconds.")
        if not isinstance(api_key_env, str) or not api_key_env.isidentifier():
            raise ValueError("API key environment variable name is invalid.")
        self.model = model.strip()
        self.api_key_env = api_key_env
        self.timeout = timeout
        self._environ = os.environ if environ is None else environ

    def is_available(self):
        return bool(self._environ.get(self.api_key_env, "").strip())

    def generate(self, prompt: str) -> str:
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("Prompt must be non-empty text.")
        key = self._environ.get("GEMINI_API_KEY", "").strip()
        if not key:
            raise RuntimeError("Gemini API is not configured; set GEMINI_API_KEY.")
        payload = json.dumps({"contents": [{"role": "user", "parts": [{"text": prompt.strip()[:20000]}]}], "generationConfig": {"maxOutputTokens": 2048}}).encode("utf-8")
        url = "https://generativelanguage.googleapis.com/v1beta/models/" + quote(self.model, safe="-._") + ":generateContent"
        request = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json", "Accept": "application/json", "x-goog-api-key": key}, method="POST")
        try:
            opener = urllib.request.build_opener(_RejectRedirects())
            with opener.open(request, timeout=self.timeout) as response:
                raw = response.read(1000001)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise RuntimeError("Gemini API request failed.") from exc
        if len(raw) > 1000000:
            raise RuntimeError("Gemini API response exceeded the 1 MB limit.")
        try:
            data = json.loads(raw.decode("utf-8"))
            parts = data["candidates"][0]["content"]["parts"]
            answer = "".join(p.get("text", "") for p in parts if isinstance(p, dict))
        except (UnicodeError, json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
            raise RuntimeError("Gemini API returned an invalid response.") from exc
        if not answer.strip():
            raise RuntimeError("Gemini API returned an empty response.")
        return answer[:50000]
