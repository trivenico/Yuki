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


# Resolve once, reject mixed/private DNS answers, and connect to the validated
# address directly. TLS still authenticates the original hostname.
import http.client
import socket
import ssl


def _resolve_public_address(host: str, port: int) -> str:
    try:
        answers = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise ValueError("Cloud provider hostname could not be resolved safely.") from exc
    addresses = set()
    for answer in answers:
        try:
            address = ipaddress.ip_address(answer[4][0].split("%", 1)[0])
        except (ValueError, IndexError, TypeError) as exc:
            raise ValueError("Cloud provider DNS returned an invalid address.") from exc
        if not address.is_global or address.is_multicast or address.is_unspecified:
            raise ValueError("Cloud provider DNS resolved to a non-public address.")
        addresses.add(str(address))
    if not addresses:
        raise ValueError("Cloud provider hostname has no usable public address.")
    return sorted(addresses)[0]


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host, pinned_ip, **kwargs):
        super().__init__(host, **kwargs)
        self._pinned_ip = pinned_ip

    def connect(self):
        raw = socket.create_connection((self._pinned_ip, self.port), self.timeout, self.source_address)
        try:
            self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
        except Exception:
            raw.close()
            raise


class _PinnedHTTPSHandler(urllib.request.HTTPSHandler):
    def __init__(self, pinned_ip):
        super().__init__(context=ssl.create_default_context())
        self._pinned_ip = pinned_ip

    def https_open(self, request):
        return self.do_open(
            lambda host, **kwargs: _PinnedHTTPSConnection(host, self._pinned_ip, **kwargs),
            request,
        )


def _build_pinned_opener(base_url: str):
    parsed = urlsplit(base_url)
    host = (parsed.hostname or "").rstrip(".").lower()
    port = parsed.port or 443
    pinned_ip = _resolve_public_address(host, port)
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        _PinnedHTTPSHandler(pinned_ip),
        _RejectRedirects(),
    )


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
            opener = _build_pinned_opener(self.base_url)
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

    def generate_chat(self, *, messages, tools, tool_choice="auto") -> dict:
        """Generate from a role-preserving transcript for AgentExecutionLoop."""
        if not isinstance(messages, (list, tuple)) or not messages or len(messages) > 128:
            raise ValueError("messages must contain 1 to 128 entries.")
        normalized_messages = []
        total_chars = 0
        for item in messages:
            if not isinstance(item, dict) or set(item) != {"role", "content"}:
                raise ValueError("Each message must contain only role and content.")
            if not isinstance(item["role"], str) or item["role"] not in {"system", "user", "assistant"} or not isinstance(item["content"], str):
                raise ValueError("Message role or content is invalid.")
            total_chars += len(item["content"])
            if total_chars > 100_000:
                raise ValueError("Message transcript exceeds the size limit.")
            normalized_messages.append({"role": item["role"], "content": item["content"]})
        if not isinstance(tools, list) or len(tools) > 32:
            raise ValueError("tools must be a list containing at most 32 definitions.")
        if tool_choice not in ("auto", "none", "required"):
            raise ValueError("tool_choice must be auto, none, or required.")
        normalized_tools = []
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
            if (not isinstance(name, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,127}", name)
                    or name in names or not isinstance(description, str) or len(description) > 1024
                    or not isinstance(parameters, dict)
                    or len(json.dumps(parameters, separators=(",", ":"))) > 8_000):
                raise ValueError("Function tool definition is invalid.")
            names.add(name)
            normalized_tools.append({"type": "function", "function": {
                "name": name, "description": description, "parameters": parameters,
                "strict": function.get("strict", True) is True,
            }})
        if tool_choice == "required" and not normalized_tools:
            raise ValueError("At least one tool is required.")
        api_key = self._environ.get(self.api_key_env, "").strip()
        if not api_key:
            raise RuntimeError(f"Cloud provider is not configured; set {self.api_key_env}.")
        payload = json.dumps({
            "model": self.model, "messages": normalized_messages,
            "tools": normalized_tools, "tool_choice": tool_choice, "stream": False,
        }).encode("utf-8")
        request = urllib.request.Request(
            self.base_url + "/chat/completions", data=payload,
            headers={"Content-Type": "application/json", "Accept": "application/json",
                     "Authorization": "Bearer " + api_key}, method="POST",
        )
        try:
            opener = _build_pinned_opener(self.base_url)
            with opener.open(request, timeout=self.timeout) as response:
                raw = response.read(2_000_001)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise RuntimeError("Cloud provider chat request failed; redirects are not followed.") from exc
        if len(raw) > 2_000_000:
            raise RuntimeError("Cloud provider response exceeded the 2 MB limit.")
        try:
            body = json.loads(raw.decode("utf-8"))
            choice = body["choices"][0]
            message = choice["message"]
        except (UnicodeDecodeError, json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
            raise RuntimeError("Cloud provider returned an unsupported chat response.") from exc
        content = message.get("content")
        if content is not None and not isinstance(content, str):
            raise RuntimeError("Cloud provider returned invalid message content.")
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
        from core.structured_tool_protocol import parse_tool_calls
        try:
            parse_tool_calls(json.dumps(envelope, ensure_ascii=False))
        except ValueError as exc:
            raise RuntimeError("Cloud provider returned tool calls outside Yuki's protocol limits.") from exc
        return {"text": content or "", "tool_calls": envelope, "finish_reason": choice.get("finish_reason")}


    def generate_chat_stream(self, *, messages, tools, tool_choice="auto"):
        """Yield normalized text deltas and one validated final response from SSE."""
        if not isinstance(messages, (list, tuple)) or not messages or len(messages) > 128:
            raise ValueError("messages must contain 1 to 128 entries.")
        normalized_messages = []
        total_chars = 0
        for item in messages:
            if not isinstance(item, dict) or set(item) != {"role", "content"}:
                raise ValueError("Each message must contain only role and content.")
            if item["role"] not in {"system", "user", "assistant"} or not isinstance(item["content"], str):
                raise ValueError("Message role or content is invalid.")
            total_chars += len(item["content"])
            if total_chars > 100_000:
                raise ValueError("Message transcript exceeds the size limit.")
            normalized_messages.append({"role": item["role"], "content": item["content"]})
        if not isinstance(tools, list) or len(tools) > 32:
            raise ValueError("tools must be a list containing at most 32 definitions.")
        if tool_choice not in ("auto", "none", "required"):
            raise ValueError("tool_choice must be auto, none, or required.")
        names = set()
        normalized_tools = []
        for item in tools:
            if (not isinstance(item, dict) or set(item) != {"type", "function"}
                    or item["type"] != "function" or not isinstance(item["function"], dict)):
                raise ValueError("Each tool must use the OpenAI function-tool schema.")
            function = item["function"]
            name = function.get("name")
            description = function.get("description", "")
            parameters = function.get("parameters")
            if (set(function) - {"name", "description", "parameters", "strict"}
                    or not isinstance(name, str)
                    or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,127}", name)
                    or name in names or not isinstance(description, str) or len(description) > 1024
                    or not isinstance(parameters, dict)
                    or len(json.dumps(parameters, separators=(",", ":"))) > 8_000):
                raise ValueError("Function tool definition is invalid.")
            names.add(name)
            normalized_tools.append({"type": "function", "function": {
                "name": name, "description": description, "parameters": parameters,
                "strict": function.get("strict", True) is True,
            }})
        if tool_choice == "required" and not normalized_tools:
            raise ValueError("At least one tool is required.")
        api_key = self._environ.get(self.api_key_env, "").strip()
        if not api_key:
            raise RuntimeError(f"Cloud provider is not configured; set {self.api_key_env}.")
        payload = json.dumps({
            "model": self.model, "messages": normalized_messages,
            "tools": normalized_tools, "tool_choice": tool_choice, "stream": True,
        }).encode("utf-8")
        request = urllib.request.Request(
            self.base_url + "/chat/completions", data=payload,
            headers={"Content-Type": "application/json", "Accept": "text/event-stream",
                     "Authorization": "Bearer " + api_key}, method="POST",
        )
        text_parts = []
        text_size = 0
        tool_parts = {}
        total_bytes = 0
        finish_reason = None
        saw_done = False
        try:
            opener = _build_pinned_opener(self.base_url)
            with opener.open(request, timeout=self.timeout) as response:
                while True:
                    line = response.readline(65_537)
                    if not line:
                        break
                    total_bytes += len(line)
                    if len(line) > 65_536 or total_bytes > 2_000_000:
                        raise RuntimeError("Cloud provider stream exceeded the response limit.")
                    line = line.strip()
                    if not line or line.startswith(b":") or not line.startswith(b"data:"):
                        continue
                    data = line[5:].strip()
                    if data == b"[DONE]":
                        saw_done = True
                        break
                    try:
                        chunk = json.loads(data.decode("utf-8"))
                        choice = chunk["choices"][0]
                        delta = choice.get("delta", {})
                    except (UnicodeDecodeError, json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
                        raise RuntimeError("Cloud provider returned a malformed stream event.") from exc
                    if not isinstance(delta, dict):
                        raise RuntimeError("Cloud provider returned an invalid stream delta.")
                    content = delta.get("content")
                    if content is not None:
                        if not isinstance(content, str) or not content:
                            raise RuntimeError("Cloud provider returned invalid streamed text.")
                        text_size += len(content)
                        if text_size > 24_000:
                            raise RuntimeError("Cloud provider stream exceeded the text limit.")
                        text_parts.append(content)
                        yield {"type": "text_delta", "text": content}
                    calls = delta.get("tool_calls", [])
                    if not isinstance(calls, list) or len(calls) > 5:
                        raise RuntimeError("Cloud provider returned invalid streamed tool calls.")
                    for call in calls:
                        if not isinstance(call, dict) or type(call.get("index")) is not int or not 0 <= call["index"] < 5:
                            raise RuntimeError("Cloud provider returned an invalid tool-call index.")
                        index = call["index"]
                        current = tool_parts.setdefault(index, {"id": "", "name": "", "arguments": ""})
                        if "id" in call:
                            if current["id"] and current["id"] != call["id"]:
                                raise RuntimeError("Cloud provider changed a streamed tool-call id.")
                            current["id"] = call["id"]
                        function = call.get("function", {})
                        if not isinstance(function, dict):
                            raise RuntimeError("Cloud provider returned an invalid function delta.")
                        if "name" in function:
                            if current["name"] and current["name"] != function["name"]:
                                raise RuntimeError("Cloud provider changed a streamed tool name.")
                            current["name"] = function["name"]
                        fragment = function.get("arguments", "")
                        if not isinstance(fragment, str) or len(current["arguments"]) + len(fragment) > 8_000:
                            raise RuntimeError("Cloud provider streamed invalid or oversized tool arguments.")
                        current["arguments"] += fragment
                    if choice.get("finish_reason") is not None:
                        finish_reason = choice["finish_reason"]
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise RuntimeError("Cloud provider streaming request failed; redirects are not followed.") from exc

        if not saw_done and finish_reason is None:
            raise RuntimeError("Cloud provider stream ended before a completion event.")
        normalized_calls = []
        seen_ids = set()
        for index in sorted(tool_parts):
            call = tool_parts[index]
            try:
                call_id, name = call["id"], call["name"]
                arguments = json.loads(call["arguments"])
            except (json.JSONDecodeError, TypeError) as exc:
                raise RuntimeError("Cloud provider returned malformed streamed tool-call data.") from exc
            if (not isinstance(call_id, str) or not call_id or len(call_id) > 64
                    or call_id in seen_ids or name not in names or not isinstance(arguments, dict)):
                raise RuntimeError("Cloud provider returned an unrecognized streamed tool call.")
            seen_ids.add(call_id)
            normalized_calls.append({"id": call_id, "name": name, "arguments": arguments})
        envelope = {"tool_calls": normalized_calls}
        from core.structured_tool_protocol import parse_tool_calls
        try:
            parse_tool_calls(json.dumps(envelope, ensure_ascii=False))
        except ValueError as exc:
            raise RuntimeError("Cloud provider returned tool calls outside Yuki's protocol limits.") from exc
        yield {"type": "response", "response": {
            "text": "".join(text_parts), "tool_calls": envelope, "finish_reason": finish_reason,
        }}

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
            opener = _build_pinned_opener(self.base_url)
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
