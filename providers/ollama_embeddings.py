"""Local Ollama embedding adapter for semantic memory; no cloud fallback."""
from __future__ import annotations

import json
import math
import urllib.error
import urllib.request
from urllib.parse import urlsplit


class OllamaEmbeddingProvider:
    """Callable text embedder using Ollama POST /api/embed."""

    def __init__(self, base_url="http://127.0.0.1:11434", model="", timeout=30):
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
            raise ValueError("Configure a valid Ollama embedding model name.")
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 1 <= timeout <= 120:
            raise ValueError("Timeout must be between 1 and 120 seconds.")
        self.base_url = base_url.strip().rstrip("/")
        self.model = model.strip()
        self.timeout = timeout

    def __call__(self, text: str) -> tuple[float, ...]:
        if not isinstance(text, str) or not text.strip() or len(text) > 12000:
            raise ValueError("Embedding input must be non-empty bounded text.")
        payload = json.dumps({"model": self.model, "input": text.strip()}).encode("utf-8")
        request = urllib.request.Request(
            self.base_url + "/api/embed", data=payload,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read(1_000_001)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise RuntimeError("Local Ollama embedding request failed.") from exc
        if len(raw) > 1_000_000:
            raise RuntimeError("Ollama embedding response exceeded the 1 MB limit.")
        try:
            body = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("Ollama returned invalid embedding JSON.") from exc
        embeddings = body.get("embeddings") if isinstance(body, dict) else None
        if not isinstance(embeddings, list) or len(embeddings) != 1 or not isinstance(embeddings[0], list):
            raise RuntimeError("Ollama returned an invalid embedding batch.")
        try:
            vector = tuple(float(value) for value in embeddings[0])
        except (TypeError, ValueError, OverflowError) as exc:
            raise RuntimeError("Ollama returned non-numeric embedding values.") from exc
        if not vector or len(vector) > 16384 or any(not math.isfinite(value) for value in vector):
            raise RuntimeError("Ollama returned an invalid embedding vector.")
        return vector
