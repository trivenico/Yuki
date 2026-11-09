"""Local Ollama chat/generation adapter using only the Python standard library."""
from __future__ import annotations

import json
import urllib.error
import urllib.request
import re
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
        """Return true only when the local server responds and the model is installed."""
        request = urllib.request.Request(
            self.base_url + "/api/tags",
            headers={"Accept": "application/json"},
            method="GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=min(self.timeout, 5)) as response:
                raw = response.read(500_001)
        except (urllib.error.URLError, TimeoutError, OSError):
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
        class RejectRedirects(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                return None
        try:
            opener = urllib.request.build_opener(RejectRedirects())
            with opener.open(request, timeout=self.timeout) as response:
                raw = response.read(2_000_001)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
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
        usage = _normalize_ollama_usage(body)
        return {"text": content, "tool_calls": envelope,
                "finish_reason": "tool_calls" if calls else "stop", "usage": usage}


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
        class RejectRedirects(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                return None
        text_parts = []
        text_size = 0
        normalized_calls = []
        total_bytes = 0
        done = False
        usage = None
        try:
            opener = urllib.request.build_opener(RejectRedirects())
            with opener.open(request, timeout=self.timeout) as response:
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
                        usage = _normalize_ollama_usage(chunk)
                        break
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
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
            "finish_reason": finish_reason, "usage": usage,
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


def _normalize_ollama_usage(payload):
    """Return exact Ollama token counts when the server reports both counters."""
    if not isinstance(payload, dict):
        return None
    prompt = payload.get("prompt_eval_count")
    completion = payload.get("eval_count")
    if (type(prompt) is not int or prompt < 0
            or type(completion) is not int or completion < 0):
        return None
    return {"input_tokens": prompt, "output_tokens": completion, "estimated": False}
