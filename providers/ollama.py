"""Local Ollama chat/generation adapter using only the Python standard library."""
from __future__ import annotations

import json
import urllib.error
import urllib.request

from providers.http_client import open_provider_request
from urllib.parse import urlsplit


class OllamaProvider:
    """Talk to a local or HTTPS Ollama endpoint; never silently routes to cloud."""

    def __init__(
        self, base_url="http://127.0.0.1:11434", model="", timeout=45, *, auto_select=False
    ):
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
        if not isinstance(model, str) or len(model) > 120:
            raise ValueError("Configure a valid Ollama model name.")
        if not model.strip() and not auto_select:
            raise ValueError("Configure a valid Ollama model name or enable automatic selection.")
        if not isinstance(auto_select, bool):
            raise ValueError("auto_select must be a boolean.")
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 1 <= timeout <= 120:
            raise ValueError("Timeout must be between 1 and 120 seconds.")
        self.base_url = base_url.strip().rstrip("/")
        self.model = model.strip()
        self.timeout = timeout
        self.auto_select = auto_select

    def list_models(self):
        """Return bounded metadata for locally installed Ollama models."""
        request = urllib.request.Request(
            self.base_url + "/api/tags",
            headers={"Accept": "application/json"},
            method="GET",
        )
        try:
            with open_provider_request(request, timeout=min(self.timeout, 5)) as response:
                raw = response.read(500_001)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise RuntimeError("Ollama model inventory is unavailable.") from exc
        if len(raw) > 500_000:
            raise RuntimeError("Ollama model inventory exceeded the response limit.")
        try:
            body = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("Ollama returned an invalid model inventory.") from exc
        models = body.get("models") if isinstance(body, dict) else None
        if not isinstance(models, list):
            raise RuntimeError("Ollama returned an invalid model inventory.")
        result = []
        for item in models:
            if not isinstance(item, dict):
                continue
            name = item.get("name")
            if not isinstance(name, str) or not name.strip():
                continue
            model = {"name": name.strip()}
            size = item.get("size")
            if isinstance(size, int) and not isinstance(size, bool) and size > 0:
                model["size"] = size
            result.append(model)
        return result

    def select_model_for_hardware(self):
        """Choose an installed model only when its size fits a conservative RAM estimate."""
        from core.hardware import detect_hardware, recommend_installed_model

        selected = recommend_installed_model(self.list_models(), detect_hardware())
        if selected is None:
            raise RuntimeError(
                "No installed Ollama model has enough verified size/memory data for safe "
                "automatic selection. Set YUKI_OLLAMA_MODEL explicitly or install a smaller model."
            )
        self.model = selected
        return selected

    def is_available(self):
        """Return true only when the local server responds and the model is installed."""
        try:
            models = self.list_models()
        except RuntimeError:
            return False
        if not self.model and self.auto_select:
            try:
                self.select_model_for_hardware()
            except RuntimeError:
                return False
        return any(
            item["name"] == self.model or item["name"].startswith(self.model + ":")
            for item in models
        ) if self.model else False

    def generate(self, prompt: str) -> str:
        if not self.model and self.auto_select:
            self.select_model_for_hardware()
        if not self.model:
            raise RuntimeError("No Ollama model is configured.")
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
            with open_provider_request(request, timeout=self.timeout) as response:
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
