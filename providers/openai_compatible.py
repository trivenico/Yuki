"""OpenAI-compatible cloud chat adapter with explicit endpoint trust boundaries."""
from __future__ import annotations
import ipaddress
import json
import os
import urllib.error
import urllib.request
from urllib.parse import urlsplit

_DEFAULT_ALLOWED_HOSTS = frozenset({
    "api.openai.com", "openrouter.ai", "api.deepseek.com",
    "api.moonshot.ai", "api.minimax.chat", "api.minimax.io",
    "api.z.ai", "api.tokenrouter.io",
})

class _RejectRedirects(urllib.request.HTTPRedirectHandler):
    """Cloud credentials and prompts must never follow redirects to another endpoint."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None

def _trusted_https_endpoint(base_url: str, allowed_hosts=None) -> str:
    if not isinstance(base_url, str) or not base_url.strip():
        raise ValueError("Cloud provider endpoint must be an HTTPS URL.")
    normalized = base_url.strip().rstrip("/")
    try:
        parsed = urlsplit(normalized)
        host = (parsed.hostname or "").rstrip(".").lower()
        parsed.port
    except ValueError as exc:
        raise ValueError("Cloud provider endpoint URL is invalid.") from exc
    if (parsed.scheme.lower() != "https" or not host or parsed.username or parsed.password
            or parsed.query or parsed.fragment):
        raise ValueError("Cloud provider endpoint must be an HTTPS URL without credentials.")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if ip is not None and (not ip.is_global or ip.is_multicast or ip.is_unspecified):
        raise ValueError("Cloud provider endpoint cannot use a private or non-public IP address.")
    configured = os.environ.get("YUKI_CLOUD_ALLOWED_HOSTS", "")
    explicit = {item.strip().rstrip(".").lower() for item in configured.split(",") if item.strip()}
    allowed = set(_DEFAULT_ALLOWED_HOSTS) | explicit
    if allowed_hosts is not None:
        allowed.update(str(item).strip().rstrip(".").lower() for item in allowed_hosts if str(item).strip())
    if host not in allowed:
        raise ValueError("Cloud endpoint host is not trusted; add its exact hostname to YUKI_CLOUD_ALLOWED_HOSTS.")
    return normalized

class OpenAICompatibleProvider:
    """Chat Completions adapter. API keys come from environment variables only."""
    def __init__(self, model, *, api_key_env="OPENAI_API_KEY",
                 base_url="https://api.openai.com/v1", timeout=45, allowed_hosts=None,
                 environ=None):
        if not isinstance(model, str) or not model.strip() or len(model) > 120:
            raise ValueError("Configure a valid cloud model name.")
        if not isinstance(api_key_env, str) or not api_key_env.isidentifier():
            raise ValueError("API key environment variable name is invalid.")
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 1 <= timeout <= 120:
            raise ValueError("Timeout must be between 1 and 120 seconds.")
        self.base_url = _trusted_https_endpoint(base_url, allowed_hosts)
        self.model = model.strip()
        self.api_key_env = api_key_env
        self.timeout = timeout
        # Injectable environment mapping keeps provider construction deterministic in
        # tests while production defaults to the real process environment.
        self._environ = os.environ if environ is None else environ

    def is_available(self):
        return bool(self._environ.get(self.api_key_env, "").strip())

    def stream(self, prompt: str):
        """Yield bounded Server-Sent Events content from Chat Completions."""
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("Prompt must be non-empty text.")
        api_key = self._environ.get(self.api_key_env, "").strip()
        if not api_key:
            raise RuntimeError(f"Cloud provider is not configured; set {self.api_key_env}.")
        payload = json.dumps({
            "model": self.model,
            "messages": [{"role": "user", "content": prompt.strip()[:20000]}],
            "stream": True,
        }).encode("utf-8")
        request = urllib.request.Request(
            self.base_url + "/chat/completions", data=payload,
            headers={"Content-Type": "application/json", "Accept": "text/event-stream",
                     "Authorization": "Bearer " + api_key}, method="POST",
        )
        total = 0
        try:
            opener = urllib.request.build_opener(_RejectRedirects())
            with opener.open(request, timeout=self.timeout) as response:
                while True:
                    line = response.readline(256_001)
                    if not line:
                        break
                    total += len(line)
                    if len(line) > 256_000 or total > 2_000_000:
                        raise RuntimeError("Cloud stream exceeded the response limit.")
                    line = line.decode("utf-8", errors="strict").strip()
                    if not line or line.startswith(":") or not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        return
                    try:
                        event = json.loads(data)
                        choices = event.get("choices", [])
                        delta = choices[0].get("delta", {}) if choices else {}
                        chunk = delta.get("content", "")
                    except (UnicodeDecodeError, json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
                        raise RuntimeError("Cloud provider returned an unsupported stream event.") from exc
                    if chunk:
                        if not isinstance(chunk, str):
                            raise RuntimeError("Cloud provider emitted a non-text chunk.")
                        yield chunk
        except (urllib.error.URLError, TimeoutError, OSError, UnicodeDecodeError) as exc:
            raise RuntimeError("Cloud provider stream failed; redirects are not followed.") from exc

    def generate(self, prompt: str) -> str:
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError("Prompt must be non-empty text.")
        api_key = self._environ.get(self.api_key_env, "").strip()
        if not api_key:
            raise RuntimeError(f"Cloud provider is not configured; set {self.api_key_env}.")
        payload = json.dumps({
            "model": self.model,
            "messages": [{"role": "user", "content": prompt.strip()[:20000]},
            ],
            "stream": False,
        }).encode("utf-8")
        request = urllib.request.Request(
            self.base_url + "/chat/completions", data=payload,
            headers={"Content-Type": "application/json", "Accept": "application/json",
                     "Authorization": "Bearer " + api_key}, method="POST",
        )
        try:
            # Deliberately disable automatic redirects: even a valid initial host must
            # not redirect credentials/prompts to an unreviewed destination.
            opener = urllib.request.build_opener(_RejectRedirects())
            with opener.open(request, timeout=self.timeout) as response:
                raw = response.read(2_000_001)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise RuntimeError("Cloud provider request failed; redirects are not followed.") from exc
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
