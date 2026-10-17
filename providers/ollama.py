"""Local Ollama chat/generation adapter using only the Python standard library."""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from urllib.parse import urlsplit


class OllamaProvider:
    """Talk to a local or HTTPS Ollama endpoint; never silently routes to cloud."""

    def __init__(self, base_url="http://127.0.0.1:11434", model="", timeout=45):
        if not isinstance(base_url, str) or not base_url.strip():
            raise ValueError("Ollama base URL is required.")
        parsed = urlsplit(base_url.strip().rstrip("/"))
        host = parsed.hostname
        try:
            parsed.port
        except ValueError as exc:
            raise ValueError("Invalid Ollama port.") from exc
        loopback = host == "localhost"
        if host:
            import ipaddress
            try:
                loopback = loopback or ipaddress.ip_address(host).is_loopback
            except ValueError:
                pass
        if (parsed.scheme != "https" and not (parsed.scheme == "http" and loopback)
                or not host or parsed.username or parsed.password or parsed.query or parsed.fragment):
            raise ValueError("Ollama must use HTTPS or loopback HTTP without URL credentials.")
        if not isinstance(model, str) or not model.strip() or len(model) > 120:
            raise ValueError("Configure a valid Ollama model name.")
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 1 <= timeout <= 120:
            raise ValueError("Timeout must be between 1 and 120 seconds.")
        self.base_url = base_url.strip().rstrip("/")
        self.model = model.strip()
        self.timeout = timeout

    def is_available(self):
        # A configured endpoint is not proof the server/model is reachable.
        return True

    def generate(self, prompt: str) -> str:
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("Prompt must be non-empty text.")
        payload = json.dumps({
            "model": self.model,
            "prompt": prompt.strip()[:20000],
            "stream": False,
        }).encode("utf-8")
        request = urllib.request.Request(
            self.base_url + "/api/generate",
            data=payload,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read(2_000_001)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise RuntimeError("Local Ollama request failed.") from exc
        if len(raw) > 2_000_000:
            raise RuntimeError("Ollama response exceeded the 2 MB limit.")
        try:
            body = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("Ollama returned invalid JSON.") from exc
        text = body.get("response") if isinstance(body, dict) else None
        if not isinstance(text, str) or not text.strip():
            raise RuntimeError("Ollama returned no text response.")
        return text.strip()
