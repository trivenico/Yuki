"""Local Colibri inference adapter (OpenAI-compatible HTTP API).

Colibri is an optional external runtime. Yuki never downloads model weights,
builds native kernels, or starts/stops Colibri automatically.
"""
from __future__ import annotations

import ipaddress
import json
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from providers.base import BaseProvider


class ColibriProvider(BaseProvider):
    """Connect to a Colibri server bound to loopback on the same machine."""

    def __init__(self, model: str, *, base_url="http://127.0.0.1:8000/v1",
                 timeout=120, api_key="yuki-local"):
        if not isinstance(model, str) or not model.strip() or len(model) > 120:
            raise ValueError("Configure a valid Colibri model id (use the id reported by coli status).")
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 1 <= timeout <= 600:
            raise ValueError("Colibri timeout must be between 1 and 600 seconds.")
        self.base_url = self._validate_base_url(base_url)
        self.model = model.strip()
        self.timeout = float(timeout)
        self.api_key = str(api_key or "yuki-local")[:256]

    @staticmethod
    def _validate_base_url(value):
        if not isinstance(value, str) or len(value) > 2048:
            raise ValueError("Colibri URL must be a bounded loopback URL.")
        parsed = urlsplit(value.strip().rstrip("/"))
        host = (parsed.hostname or "").lower().rstrip(".")
        try:
            port = parsed.port
            address = ipaddress.ip_address(host)
            loopback = address.is_loopback
        except ValueError:
            port = parsed.port
            loopback = host == "localhost"
        if (parsed.scheme != "http" or not loopback or parsed.username or parsed.password
                or parsed.query or parsed.fragment or (port is not None and not 1 <= port <= 65535)):
            raise ValueError("Colibri must use plain HTTP on localhost/127.0.0.1; remote endpoints are blocked.")
        path = parsed.path.rstrip("/")
        if path and path != "/v1":
            raise ValueError("Colibri base URL path must be empty or /v1.")
        return f"http://{host}:{port or 8000}/v1"

    def is_available(self) -> bool:
        request = urllib.request.Request(
            self.base_url + "/models",
            headers={"Accept": "application/json", "Authorization": "Bearer " + self.api_key},
        )
        try:
            with urllib.request.urlopen(request, timeout=min(self.timeout, 3)) as response:
                body = response.read(256_000)
            data = json.loads(body.decode("utf-8"))
            return isinstance(data, dict) and isinstance(data.get("data"), list)
        except (OSError, TimeoutError, urllib.error.URLError, UnicodeDecodeError, json.JSONDecodeError):
            return False

    def generate(self, prompt: str) -> str:
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("Prompt must be non-empty text.")
        payload = json.dumps({
            "model": self.model,
            "messages": [{"role": "user", "content": prompt.strip()[:20000]}],
            "stream": False,
        }).encode("utf-8")
        request = urllib.request.Request(
            self.base_url + "/chat/completions",
            data=payload,
            headers={"Content-Type": "application/json", "Accept": "application/json",
                     "Authorization": "Bearer " + self.api_key},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read(2_000_001)
        except (OSError, TimeoutError, urllib.error.URLError) as exc:
            raise RuntimeError("Colibri request failed; check that the local server is running.") from exc
        if len(raw) > 2_000_000:
            raise RuntimeError("Colibri response exceeded the 2 MB limit.")
        try:
            body = json.loads(raw.decode("utf-8"))
            content = body["choices"][0]["message"]["content"]
        except (UnicodeDecodeError, json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
            raise RuntimeError("Colibri returned an unsupported chat-completions response.") from exc
        if isinstance(content, list):
            content = "".join(
                item.get("text", "") for item in content
                if isinstance(item, dict) and item.get("type") == "text"
            )
        if not isinstance(content, str) or not content.strip():
            raise RuntimeError("Colibri returned no text response.")
        return content.strip()
