"""Native Anthropic Messages API adapter with bounded SSE streaming."""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from urllib.parse import urlsplit


class _RejectRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class AnthropicProvider:
    """Anthropic Messages API adapter; credentials are read from environment only."""

    def __init__(
        self, model, *, api_key_env="ANTHROPIC_API_KEY",
        base_url="https://api.anthropic.com", timeout=60, environ=None,
    ):
        if not isinstance(model, str) or not model.strip() or len(model) > 120:
            raise ValueError("Configure a valid Anthropic model name.")
        if not isinstance(api_key_env, str) or not api_key_env.isidentifier():
            raise ValueError("API key environment variable name is invalid.")
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 1 <= timeout <= 120:
            raise ValueError("Timeout must be between 1 and 120 seconds.")
        parsed = urlsplit(str(base_url).strip().rstrip("/"))
        if (parsed.scheme != "https" or parsed.hostname != "api.anthropic.com"
                or parsed.username or parsed.password or parsed.query or parsed.fragment):
            raise ValueError("Anthropic endpoint must be the trusted api.anthropic.com HTTPS origin.")
        self.base_url = "https://api.anthropic.com"
        self.model = model.strip()
        self.api_key_env = api_key_env
        self.timeout = timeout
        self._environ = os.environ if environ is None else environ

    def is_available(self):
        return bool(self._environ.get(self.api_key_env, "").strip())

    def _normalize(self, messages, tools, tool_choice):
        if not isinstance(messages, (list, tuple)) or not 1 <= len(messages) <= 128:
            raise ValueError("messages must contain 1 to 128 entries.")
        system_parts, normalized_messages = [], []
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
            if role == "system":
                system_parts.append(content)
            else:
                normalized_messages.append({"role": role, "content": content})
        if not normalized_messages:
            raise ValueError("At least one user or assistant message is required.")
        if not isinstance(tools, (list, tuple)) or len(tools) > 32:
            raise ValueError("tools must contain at most 32 definitions.")
        if tool_choice not in {"auto", "none", "required"}:
            raise ValueError("tool_choice is invalid.")
        names, normalized_tools = set(), []
        for item in tools:
            if not isinstance(item, dict) or set(item) != {"type", "function"} or item["type"] != "function":
                raise ValueError("Each tool must use the function-tool schema.")
            function = item["function"]
            if not isinstance(function, dict) or set(function) - {"name", "description", "parameters", "strict"}:
                raise ValueError("Function tool definition contains unsupported fields.")
            name = function.get("name")
            description = function.get("description", "")
            schema = function.get("parameters")
            if (not isinstance(name, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,127}", name)
                    or name in names or not isinstance(description, str) or len(description) > 1024
                    or not isinstance(schema, dict)
                    or len(json.dumps(schema, separators=(",", ":"))) > 8_000):
                raise ValueError("Function tool definition is invalid.")
            names.add(name)
            normalized_tools.append({
                "name": name, "description": description, "input_schema": schema,
            })
        if tool_choice == "required" and not normalized_tools:
            raise ValueError("At least one tool is required.")
        payload = {
            "model": self.model,
            "max_tokens": 4096,
            "messages": normalized_messages,
            "stream": True,
        }
        if system_parts:
            payload["system"] = "\n\n".join(system_parts)
        if tool_choice != "none" and normalized_tools:
            payload["tools"] = normalized_tools
            if tool_choice == "required":
                payload["tool_choice"] = {"type": "any"}
            else:
                payload["tool_choice"] = {"type": "auto"}
        return payload, names

    def generate_chat_stream(self, *, messages, tools, tool_choice="auto"):
        """Yield Anthropic text deltas and one normalized response event."""
        payload, names = self._normalize(messages, tools, tool_choice)
        api_key = self._environ.get(self.api_key_env, "").strip()
        if not api_key:
            raise RuntimeError(f"Anthropic provider is not configured; set {self.api_key_env}.")
        request = urllib.request.Request(
            self.base_url + "/v1/messages",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Accept": "text/event-stream",
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
            },
            method="POST",
        )
        text_parts, text_size = [], 0
        blocks, total_bytes = {}, 0
        current_event = ""
        message_stopped = False
        finish_reason = None
        try:
            opener = urllib.request.build_opener(_RejectRedirects())
            with opener.open(request, timeout=self.timeout) as response:
                while True:
                    raw_line = response.readline(65_537)
                    if not raw_line:
                        break
                    total_bytes += len(raw_line)
                    if len(raw_line) > 65_536 or total_bytes > 2_000_000:
                        raise RuntimeError("Anthropic stream exceeded the response limit.")
                    line = raw_line.decode("utf-8").strip()
                    if not line:
                        current_event = ""
                        continue
                    if line.startswith(":"):
                        continue
                    if line.startswith("event:"):
                        current_event = line[6:].strip()
                        continue
                    if not line.startswith("data:"):
                        continue
                    try:
                        data = json.loads(line[5:].strip())
                    except json.JSONDecodeError as exc:
                        raise RuntimeError("Anthropic returned malformed stream JSON.") from exc
                    if current_event == "error" or data.get("type") == "error":
                        raise RuntimeError("Anthropic reported a streaming error.")
                    if current_event == "content_block_start":
                        index = data.get("index")
                        block = data.get("content_block", {})
                        if type(index) is not int or not isinstance(block, dict):
                            raise RuntimeError("Anthropic returned an invalid content block.")
                        if block.get("type") == "tool_use":
                            if len(blocks) >= 5 or index in blocks:
                                raise RuntimeError("Anthropic returned too many or duplicate tool blocks.")
                            blocks[index] = {
                                "id": block.get("id"),
                                "name": block.get("name"),
                                "input": block.get("input", {}),
                                "partial_json": "",
                            }
                    elif current_event == "content_block_delta":
                        index = data.get("index")
                        delta = data.get("delta", {})
                        if not isinstance(delta, dict):
                            raise RuntimeError("Anthropic returned an invalid content delta.")
                        if delta.get("type") == "text_delta":
                            fragment = delta.get("text")
                            if not isinstance(fragment, str) or not fragment:
                                raise RuntimeError("Anthropic returned invalid streamed text.")
                            text_size += len(fragment)
                            if text_size > 24_000:
                                raise RuntimeError("Anthropic stream exceeded the text limit.")
                            text_parts.append(fragment)
                            yield {"type": "text_delta", "text": fragment}
                        elif delta.get("type") == "input_json_delta":
                            block = blocks.get(index)
                            fragment = delta.get("partial_json")
                            if block is None or not isinstance(fragment, str):
                                raise RuntimeError("Anthropic returned an orphaned tool-argument delta.")
                            if len(block["partial_json"]) + len(fragment) > 8_000:
                                raise RuntimeError("Anthropic tool arguments exceeded the size limit.")
                            block["partial_json"] += fragment
                    elif current_event == "message_delta":
                        delta = data.get("delta", {})
                        if isinstance(delta, dict):
                            finish_reason = delta.get("stop_reason")
                    elif current_event == "message_stop":
                        message_stopped = True
                    elif current_event in {"message_start", "content_block_stop", "ping"}:
                        continue
        except (urllib.error.URLError, TimeoutError, OSError, UnicodeDecodeError) as exc:
            raise RuntimeError("Anthropic streaming request failed; redirects are not followed.") from exc
        if not message_stopped:
            raise RuntimeError("Anthropic stream ended before message_stop.")
        normalized_calls = []
        seen_ids = set()
        for index in sorted(blocks):
            block = blocks[index]
            call_id, name = block["id"], block["name"]
            if (not isinstance(call_id, str) or not call_id or len(call_id) > 64
                    or call_id in seen_ids or name not in names):
                raise RuntimeError("Anthropic returned an unrecognized tool proposal.")
            seen_ids.add(call_id)
            if block["partial_json"]:
                try:
                    arguments = json.loads(block["partial_json"])
                except json.JSONDecodeError as exc:
                    raise RuntimeError("Anthropic returned malformed tool arguments.") from exc
            else:
                arguments = block["input"]
            if not isinstance(arguments, dict):
                raise RuntimeError("Anthropic tool arguments must be a JSON object.")
            normalized_calls.append({"id": call_id, "name": name, "arguments": arguments})
        if tool_choice == "none" and normalized_calls:
            raise RuntimeError("Anthropic returned tool calls when tool_choice was none.")
        if tool_choice == "required" and not normalized_calls:
            raise RuntimeError("Anthropic did not return a required tool call.")
        envelope = {"tool_calls": normalized_calls}
        from core.structured_tool_protocol import parse_tool_calls
        try:
            parse_tool_calls(json.dumps(envelope, ensure_ascii=False))
        except ValueError as exc:
            raise RuntimeError("Anthropic tool proposals exceeded Yuki's protocol limits.") from exc
        yield {"type": "response", "response": {
            "text": "".join(text_parts),
            "tool_calls": envelope,
            "finish_reason": finish_reason,
        }}

    def generate_chat(self, *, messages, tools, tool_choice="auto"):
        """Collect a native stream into the standard provider chat envelope."""
        final = None
        for event in self.generate_chat_stream(messages=messages, tools=tools, tool_choice=tool_choice):
            if event.get("type") == "response":
                final = event["response"]
        if final is None:
            raise RuntimeError("Anthropic returned no completed response.")
        return final

    def generate(self, prompt: str) -> str:
        if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 20_000:
            raise ValueError("Prompt must be non-empty text of at most 20000 characters.")
        return self.generate_chat(
            messages=[{"role": "user", "content": prompt.strip()}],
            tools=[], tool_choice="none",
        )["text"]
