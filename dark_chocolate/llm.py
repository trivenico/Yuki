"""Local-first async Ollama chat client with bounded response reads."""
from __future__ import annotations
import asyncio
import json
import urllib.error
import urllib.request
from typing import Any

class OllamaError(RuntimeError):
    pass
class OllamaClient:
    def __init__(self, base_url: str = "http://127.0.0.1:11434", model: str = "llama3.1:8b", timeout: float = 60.0) -> None:
        allowed = base_url.startswith(("http://127.0.0.1", "http://localhost", "http://[::1]", "https://"))
        if not allowed or "@" in base_url:
            raise ValueError("Ollama endpoint must be loopback HTTP or HTTPS without credentials")
        self.base_url, self.model, self.timeout = base_url.rstrip("/"), model, timeout
    async def chat(self, messages: list[dict[str, str]], system: str | None = None) -> str:
        items = ([{"role": "system", "content": system}] if system else []) + messages
        payload = json.dumps({"model": self.model, "messages": items, "stream": False}).encode()
        def request() -> str:
            req = urllib.request.Request(self.base_url + "/api/chat", data=payload, headers={"Content-Type": "application/json"}, method="POST")
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as response:
                    raw = response.read(2_000_001)
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                raise OllamaError("Local Ollama request failed") from exc
            if len(raw) > 2_000_000:
                raise OllamaError("Ollama response exceeded limit")
            try:
                data: Any = json.loads(raw)
                result = data["message"]["content"]
            except (json.JSONDecodeError, KeyError, TypeError) as exc:
                raise OllamaError("Malformed Ollama response") from exc
            if not isinstance(result, str):
                raise OllamaError("Malformed Ollama message")
            return result
        return await asyncio.wait_for(asyncio.to_thread(request), timeout=self.timeout + 1)
