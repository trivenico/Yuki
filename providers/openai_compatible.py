"""OpenAI-compatible cloud chat adapter with explicit endpoint trust boundaries."""
from __future__ import annotations
import ipaddress
import json
import os
import re
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

    def generate_with_tools(self, prompt: str, tools: list[dict], *, tool_choice="auto") -> dict:
        """Request OpenAI-compatible function calls and normalize them to Yuki's protocol.

        Only caller-supplied, bounded JSON schemas are sent. Returned arguments
        are parsed as JSON objects and re-serialized into the provider-neutral
        structured_tool_protocol envelope; they are not executed here.
        """
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 20_000:
            raise ValueError("Prompt must be non-empty text of at most 20000 characters.")
        if not isinstance(tools, list) or not 1 <= len(tools) <= 32:
            raise ValueError("tools must be a list containing 1 to 32 definitions.")
        if tool_choice not in ("auto", "none", "required"):
            raise ValueError("tool_choice must be auto, none, or required.")
        normalized = []
        names = set()
        for item in tools:
            if not isinstance(item, dict) or set(item) != {"type", "function"} or item["type"] != "function":
                raise ValueError("Each tool must use the OpenAI function-tool schema.")
            function = item["function"]
            if not isinstance(function, dict) or set(function) - {"name", "description", "parameters", "strict"}:
                raise ValueError("Function tool definition contains unsupported fields.")
            name = function.get("name")
            description = function.get("description", "")
            parameters = function.get("parameters")
            if (not isinstance(name, str) or not name or len(name) > 128
                    or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,127}", name)
                    or not isinstance(description, str) or len(description) > 1024
                    or not isinstance(parameters, dict)):
                raise ValueError("Function tool name, description, or parameters are invalid.")
            if name in names:
                raise ValueError("Function tool names must be unique.")
            names.add(name)
            encoded = json.dumps(parameters, separators=(",", ":"))
            if len(encoded) > 8_000:
                raise ValueError("Function parameter schema exceeds the size limit.")
            normalized.append({"type": "function", "function": {
                "name": name, "description": description, "parameters": parameters,
                "strict": function.get("strict", True) is True,
            }})
        if tool_choice == "required" and not normalized:
            raise ValueError("At least one tool is required.")
        api_key = self._environ.get(self.api_key_env, "").strip()
        if not api_key:
            raise RuntimeError(f"Cloud provider is not configured; set {self.api_key_env}.")
        payload = json.dumps({
            "model": self.model,
            "messages": [{"role": "user", "content": prompt.strip()}],
            "tools": normalized,
            "tool_choice": tool_choice,
            "stream": False,
        }).encode("utf-8")
        request = urllib.request.Request(
            self.base_url + "/chat/completions", data=payload,
            headers={"Content-Type": "application/json", "Accept": "application/json",
                     "Authorization": "Bearer " + api_key}, method="POST",
        )
        try:
            opener = urllib.request.build_opener(_RejectRedirects())
            with opener.open(request, timeout=self.timeout) as response:
                raw = response.read(2_000_001)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise RuntimeError("Cloud provider tool-call request failed; redirects are not followed.") from exc
        if len(raw) > 2_000_000:
            raise RuntimeError("Cloud provider response exceeded the 2 MB limit.")
        try:
            body = json.loads(raw.decode("utf-8"))
            message = body["choices"][0]["message"]
        except (UnicodeDecodeError, json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
            raise RuntimeError("Cloud provider returned an unsupported tool-call response.") from exc
        calls = message.get("tool_calls", [])
        if not isinstance(calls, list) or len(calls) > 5:
            raise RuntimeError("Cloud provider returned an invalid number of tool calls.")
        normalized_calls = []
        seen_ids = set()
        for call in calls:
            try:
                call_id = call["id"]
                function = call["function"]
                name = function["name"]
                arguments = json.loads(function["arguments"])
            except (TypeError, KeyError, json.JSONDecodeError) as exc:
                raise RuntimeError("Cloud provider returned malformed tool-call data.") from exc
            if (not isinstance(call_id, str) or not call_id or len(call_id) > 64
                    or call_id in seen_ids or name not in names or not isinstance(arguments, dict)):
                raise RuntimeError("Cloud provider returned an unrecognized tool call.")
            seen_ids.add(call_id)
            normalized_calls.append({"id": call_id, "name": name, "arguments": arguments})
        envelope = {"tool_calls": normalized_calls}
        # Reuse the canonical protocol validator so provider output cannot bypass
        # its stricter identifier, payload, or batch constraints.
        from core.structured_tool_protocol import parse_tool_calls
        try:
            parse_tool_calls(json.dumps(envelope, ensure_ascii=False))
        except ValueError as exc:
            raise RuntimeError("Cloud provider returned tool calls outside Yuki's protocol limits.") from exc
        return {
            "tool_calls": envelope,
            "text": message.get("content") if isinstance(message.get("content"), str) else "",
            "finish_reason": body["choices"][0].get("finish_reason"),
        }
