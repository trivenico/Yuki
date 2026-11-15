"""Local Ollama chat/generation adapter using only the Python standard library."""
from __future__ import annotations

import json
import http.client
import ipaddress
import urllib.error
import urllib.request
import re
from urllib.parse import urlsplit

from core.pinned_https_provider_transport import PinnedHTTPSProviderTransport, PinnedHTTPTransportError


class _LoopbackResponse:
    def __init__(self, response, connection): self._response, self._connection = response, connection; self.status, self.reason, self.headers = response.status, response.reason, response.headers
    def read(self, amount=-1): return self._response.read(amount)
    def readline(self, amount=-1): return self._response.readline(amount)
    def close(self):
        try: self._response.close()
        finally: self._connection.close()
    def __enter__(self): return self
    def __exit__(self, *_): self.close(); return False

class _LoopbackHTTPTransport:
    """Connect only to literal loopback IPs; never resolve hostnames."""
    MAX_REQUEST_BYTES = 2_000_000
    def __init__(self, allowed_host, port):
        self.allowed_host, self.port = allowed_host, port
        self._ip = "127.0.0.1" if allowed_host == "localhost" else str(ipaddress.ip_address(allowed_host))
        if not ipaddress.ip_address(self._ip).is_loopback: raise ValueError("Local Ollama HTTP requires a loopback address.")
    def open(self, request, timeout=None):
        try:
            parsed = urlsplit(request.full_url)
            if parsed.scheme != "http" or parsed.hostname != self.allowed_host or (parsed.port or 80) != self.port or parsed.username or parsed.password or parsed.fragment: raise PinnedHTTPTransportError("ollama_loopback_destination_blocked")
            method, body = request.get_method(), request.data
            if not isinstance(method, str) or not re.fullmatch(r"[A-Z][A-Z0-9_-]{0,15}", method): raise PinnedHTTPTransportError("ollama_invalid_method")
            if body is not None and (not isinstance(body, bytes) or len(body) > self.MAX_REQUEST_BYTES): raise PinnedHTTPTransportError("ollama_invalid_request_body")
            headers = {}
            for key, value in request.header_items():
                if not isinstance(key, str) or not re.fullmatch(r"[!#$%&+.^_~0-9A-Za-z-]+", key) or not isinstance(value, str) or any(ord(ch) < 0x20 or ord(ch) == 0x7f for ch in value) or key.lower() in {"host", "connection", "transfer-encoding", "content-length"}: raise PinnedHTTPTransportError("ollama_invalid_headers")
                headers[key] = value
            host_header = "[" + self.allowed_host + "]" if ":" in self.allowed_host else self.allowed_host
            if self.port != 80: host_header += ":" + str(self.port)
            headers["Host"] = host_header
            connection = http.client.HTTPConnection(self._ip, self.port, timeout=timeout or 45)
            try:
                target = parsed.path or "/"
                if parsed.query: target += "?" + parsed.query
                connection.request(method, target, body=body, headers=headers)
                response = connection.getresponse()
                if 300 <= response.status < 400: raise PinnedHTTPTransportError("ollama_redirect_rejected")
                return _LoopbackResponse(response, connection)
            except Exception:
                connection.close()
                raise
        except PinnedHTTPTransportError: raise
        except (OSError, http.client.HTTPException, ValueError, TypeError): raise PinnedHTTPTransportError("ollama_loopback_unavailable") from None

class OllamaProvider:
    """Talk to a local or HTTPS Ollama endpoint; never silently routes to cloud."""

    def __init__(self, base_url="http://127.0.0.1:11434", model="", timeout=45, transport=None):
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
        port = parsed.port if parsed.port is not None else (443 if parsed.scheme == "https" else 80)
        if port < 1: raise ValueError("Invalid Ollama port.")
        if parsed.scheme == "https" and port != 443: raise ValueError("Remote Ollama HTTPS must use port 443.")
        if transport is not None and not callable(getattr(transport, "open", None)): raise TypeError("transport must provide an open(request, timeout=...) method")
        if not isinstance(model, str) or not model.strip() or len(model) > 120:
            raise ValueError("Configure a valid Ollama model name.")
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 1 <= timeout <= 120:
            raise ValueError("Timeout must be between 1 and 120 seconds.")
        self.base_url = base_url.strip().rstrip("/")
        self.model = model.strip()
        self.timeout = timeout
        if transport is not None: self._transport = transport
        elif parsed.scheme == "https": self._transport = PinnedHTTPSProviderTransport(allowed_hosts={host}, timeout=timeout)
        else: self._transport = _LoopbackHTTPTransport(host, port)

    def is_available(self):
        """Return true only when the local server responds and the model is installed."""
        request = urllib.request.Request(
            self.base_url + "/api/tags",
            headers={"Accept": "application/json"},
            method="GET",
        )
        try:
            with self._transport.open(request, timeout=min(self.timeout, 5)) as response:
                raw = response.read(500_001)
        except (PinnedHTTPTransportError, urllib.error.URLError, TimeoutError, OSError):
            return False
        if len(raw) > 500_000:
            return False
        try:
            body = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return False
        models = body.get("models") if isinstance(body, dict) else None
        if not isinstance(models, list):
            return False
        for item in models:
            if not isinstance(item, dict) or not isinstance(item.get("name"), str):
                continue
            installed = item["name"]
            if installed == self.model or installed.startswith(self.model + ":"):
                return True
        return False


    def generate_chat(self, *, messages, tools, tool_choice="auto"):
        """Generate a role-preserving chat response with normalized tool proposals."""
        if not isinstance(messages, (list, tuple)) or not 1 <= len(messages) <= 128:
            raise ValueError("messages must contain 1 to 128 entries.")
        normalized_messages = []
        total_chars = 0
        for item in messages:
            if not isinstance(item, dict) or set(item) != {"role", "content"}:
                raise ValueError("Each message must contain only role and content.")
            role, content = item["role"], item["content"]
            if not isinstance(role, str) or role not in {"system", "user", "assistant"} or not isinstance(content, str):
                raise ValueError("Message role or content is invalid.")
            total_chars += len(content)
            if total_chars > 100_000:
                raise ValueError("Message transcript exceeds the size limit.")
            normalized_messages.append({"role": role, "content": content})
        if not isinstance(tools, (list, tuple)) or len(tools) > 32:
            raise ValueError("tools must contain at most 32 definitions.")
        if tool_choice not in {"auto", "none", "required"}:
            raise ValueError("tool_choice is invalid.")
        if tool_choice == "required" and not tools:
            raise ValueError("At least one tool is required.")
        normalized_tools = []
        names = set()
        for item in tools:
            if not isinstance(item, dict) or set(item) != {"type", "function"} or item["type"] != "function":
                raise ValueError("Each tool must use the function-tool schema.")
            function = item["function"]
            if not isinstance(function, dict) or set(function) - {"name", "description", "parameters", "strict"}:
                raise ValueError("Function tool definition contains unsupported fields.")
            name, description, parameters = function.get("name"), function.get("description", ""), function.get("parameters")
            if (not isinstance(name, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,127}", name)
                    or name in names or not isinstance(description, str) or len(description) > 1024
                    or not isinstance(parameters, dict)
                    or len(json.dumps(parameters, separators=(",", ":"))) > 8_000):
                raise ValueError("Function tool definition is invalid.")
            names.add(name)
            normalized_tools.append({"type": "function", "function": {
                "name": name, "description": description, "parameters": parameters,
            }})
        payload_data = {"model": self.model, "messages": normalized_messages, "stream": False}
        if tool_choice != "none" and normalized_tools:
            payload_data["tools"] = normalized_tools
        payload = json.dumps(payload_data).encode("utf-8")
        request = urllib.request.Request(
            self.base_url + "/api/chat", data=payload,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )
                return None
        try:
            with self._transport.open(request, timeout=self.timeout) as response:
                raw = response.read(2_000_001)
        except (PinnedHTTPTransportError, urllib.error.URLError, TimeoutError, OSError) as exc:
            raise RuntimeError("Local Ollama chat request failed; redirects are not followed.") from exc
        if len(raw) > 2_000_000:
            raise RuntimeError("Ollama response exceeded the 2 MB limit.")
        try:
            body = json.loads(raw.decode("utf-8"))
            message = body["message"]
        except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError) as exc:
            raise RuntimeError("Ollama returned an unsupported chat response.") from exc
        if not isinstance(message, dict):
            raise RuntimeError("Ollama returned an invalid chat message.")
        content = message.get("content", "")
        calls = message.get("tool_calls", [])
        if not isinstance(content, str) or not isinstance(calls, list) or len(calls) > 5:
            raise RuntimeError("Ollama returned invalid chat content or tool calls.")
        normalized_calls = []
        for call in calls:
            try:
                function = call["function"]
                name, arguments = function["name"], function["arguments"]
            except (TypeError, KeyError) as exc:
                raise RuntimeError("Ollama returned malformed tool-call data.") from exc
            if name not in names:
                raise RuntimeError("Ollama returned an unrecognized tool name.")
            if not isinstance(arguments, dict):
                raise RuntimeError("Ollama returned malformed tool arguments.")
            normalized_calls.append({"id": "ollama_" + str(len(normalized_calls) + 1),
                                     "name": name, "arguments": arguments})
        if tool_choice == "none" and normalized_calls:
            raise RuntimeError("Ollama returned tool calls when tool_choice was none.")
        if tool_choice == "required" and not normalized_calls:
            raise RuntimeError("Ollama did not return a required tool call.")
        envelope = {"tool_calls": normalized_calls}
        from core.structured_tool_protocol import parse_tool_calls
        try:
            parse_tool_calls(json.dumps(envelope, ensure_ascii=False))
        except ValueError as exc:
            raise RuntimeError("Ollama returned tool calls outside Yuki's protocol limits.") from exc
        return {"text": content, "tool_calls": envelope, "finish_reason": "tool_calls" if calls else "stop"}


    def generate_chat_stream(self, *, messages, tools, tool_choice="auto"):
        """Yield normalized text deltas from Ollama's newline-delimited JSON stream."""
        if not isinstance(messages, (list, tuple)) or not 1 <= len(messages) <= 128:
            raise ValueError("messages must contain 1 to 128 entries.")
        normalized_messages = []
        total_chars = 0
        for item in messages:
            if not isinstance(item, dict) or set(item) != {"role", "content"}:
                raise ValueError("Each message must contain only role and content.")
            role, content = item["role"], item["content"]
            if role not in {"system", "user", "assistant"} or not isinstance(content, str):
                raise ValueError("Message role or content is invalid.")
            total_chars += len(content)
            if total_chars > 100_000:
                raise ValueError("Message transcript exceeds the size limit.")
            normalized_messages.append({"role": role, "content": content})
        if not isinstance(tools, (list, tuple)) or len(tools) > 32:
            raise ValueError("tools must contain at most 32 definitions.")
        if tool_choice not in {"auto", "none", "required"}:
            raise ValueError("tool_choice is invalid.")
        if tool_choice == "required" and not tools:
            raise ValueError("At least one tool is required.")
        normalized_tools = []
        names = set()
        for item in tools:
            if not isinstance(item, dict) or set(item) != {"type", "function"} or item["type"] != "function":
                raise ValueError("Each tool must use the function-tool schema.")
            function = item["function"]
            if not isinstance(function, dict) or set(function) - {"name", "description", "parameters", "strict"}:
                raise ValueError("Function tool definition contains unsupported fields.")
            name, description, parameters = function.get("name"), function.get("description", ""), function.get("parameters")
            if (not isinstance(name, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,127}", name)
                    or name in names or not isinstance(description, str) or len(description) > 1024
                    or not isinstance(parameters, dict)
                    or len(json.dumps(parameters, separators=(",", ":"))) > 8_000):
                raise ValueError("Function tool definition is invalid.")
            names.add(name)
            normalized_tools.append({"type": "function", "function": {
                "name": name, "description": description, "parameters": parameters,
            }})
        payload_data = {"model": self.model, "messages": normalized_messages, "stream": True}
        if tool_choice != "none" and normalized_tools:
            payload_data["tools"] = normalized_tools
        payload = json.dumps(payload_data).encode("utf-8")
        request = urllib.request.Request(
            self.base_url + "/api/chat", data=payload,
            headers={"Content-Type": "application/json", "Accept": "application/x-ndjson"},
            method="POST",
        )
                return None
        text_parts = []
        text_size = 0
        normalized_calls = []
        total_bytes = 0
        done = False
        try:
            with self._transport.open(request, timeout=self.timeout) as response:
                while True:
                    line = response.readline(65_537)
                    if not line:
                        break
                    total_bytes += len(line)
                    if len(line) > 65_536 or total_bytes > 2_000_000:
                        raise RuntimeError("Ollama stream exceeded the response limit.")
                    if not line.strip():
                        continue
                    try:
                        chunk = json.loads(line.decode("utf-8"))
                    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                        raise RuntimeError("Ollama returned a malformed stream event.") from exc
                    if not isinstance(chunk, dict) or not isinstance(chunk.get("done"), bool):
                        raise RuntimeError("Ollama returned an invalid stream event.")
                    message = chunk.get("message", {})
                    if not isinstance(message, dict):
                        raise RuntimeError("Ollama returned an invalid streamed message.")
                    content = message.get("content", "")
                    if not isinstance(content, str):
                        raise RuntimeError("Ollama returned invalid streamed text.")
                    if content:
                        text_size += len(content)
                        if text_size > 24_000:
                            raise RuntimeError("Ollama stream exceeded the text limit.")
                        text_parts.append(content)
                        yield {"type": "text_delta", "text": content}
                    calls = message.get("tool_calls", [])
                    if not isinstance(calls, list) or len(normalized_calls) + len(calls) > 5:
                        raise RuntimeError("Ollama returned an invalid number of streamed tool calls.")
                    for call in calls:
                        try:
                            function = call["function"]
                            name, arguments = function["name"], function["arguments"]
                        except (TypeError, KeyError) as exc:
                            raise RuntimeError("Ollama returned malformed streamed tool-call data.") from exc
                        if name not in names or not isinstance(arguments, dict):
                            raise RuntimeError("Ollama returned an unrecognized or malformed streamed tool call.")
                        normalized_calls.append({
                            "id": "ollama_" + str(len(normalized_calls) + 1),
                            "name": name, "arguments": arguments,
                        })
                    if chunk["done"]:
                        done = True
                        finish_reason = chunk.get("done_reason", "stop")
                        break
        except (PinnedHTTPTransportError, urllib.error.URLError, TimeoutError, OSError) as exc:
            raise RuntimeError("Local Ollama streaming request failed; redirects are not followed.") from exc
        if not done:
            raise RuntimeError("Ollama stream ended before its final done event.")
        if tool_choice == "none" and normalized_calls:
            raise RuntimeError("Ollama returned tool calls when tool_choice was none.")
        if tool_choice == "required" and not normalized_calls:
            raise RuntimeError("Ollama did not return a required tool call.")
        envelope = {"tool_calls": normalized_calls}
        from core.structured_tool_protocol import parse_tool_calls
        try:
            parse_tool_calls(json.dumps(envelope, ensure_ascii=False))
        except ValueError as exc:
            raise RuntimeError("Ollama returned tool calls outside Yuki's protocol limits.") from exc
        yield {"type": "response", "response": {
            "text": "".join(text_parts), "tool_calls": envelope,
            "finish_reason": finish_reason,
        }}

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
            with self._transport.open(request, timeout=self.timeout) as response:
                raw = response.read(2_000_001)
        except (PinnedHTTPTransportError, urllib.error.URLError, TimeoutError, OSError) as exc:
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
