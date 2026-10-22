"""Minimal stateless MCP Streamable HTTP bridge for ChatGPT.

Exposes only chat and status. Bind to loopback by default; remote deployment
requires an explicit opt-in, a strong bearer token, and HTTPS at the edge.
"""
from __future__ import annotations

import hmac
import json
import os
import secrets
import time
from collections import OrderedDict, deque
from threading import Lock
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlsplit

from yuki_agent import YukiAgent
from yuki_providers import ProviderManager

PROTOCOL_VERSION = "2025-03-26"
MAX_BODY_BYTES = 64 * 1024
MAX_PROMPT_CHARS = 12_000
RATE_LIMIT_REQUESTS = 60
RATE_LIMIT_WINDOW_SECONDS = 60
MAX_RATE_LIMIT_BUCKETS = 4096
MAX_BATCH_REQUESTS = 10
REQUEST_READ_TIMEOUT_SECONDS = 10
_RATE_EVENTS: OrderedDict[str, deque[float]] = OrderedDict()
_RATE_LOCK = Lock()


def _rate_limit_exceeded(peer: str, now: float | None = None) -> bool:
    """Thread-safe bounded per-peer rate limiter; evicts stale and oldest buckets."""
    current = time.monotonic() if now is None else now
    with _RATE_LOCK:
        events = _RATE_EVENTS.get(peer)
        if events is None:
            events = deque()
            _RATE_EVENTS[peer] = events
        else:
            _RATE_EVENTS.move_to_end(peer)
        while events and current - events[0] >= RATE_LIMIT_WINDOW_SECONDS:
            events.popleft()
        if len(events) >= RATE_LIMIT_REQUESTS:
            return True
        events.append(current)
        while len(_RATE_EVENTS) > MAX_RATE_LIMIT_BUCKETS:
            _RATE_EVENTS.popitem(last=False)
        return False


class BridgeConfigError(RuntimeError):
    """Bridge configuration is unsafe or incomplete."""


def _load_config(environ=None) -> dict[str, Any]:
    env = os.environ if environ is None else environ
    token = env.get("YUKI_MCP_BEARER_TOKEN", "")
    if len(token) < 32:
        raise BridgeConfigError("YUKI_MCP_BEARER_TOKEN must contain at least 32 characters.")
    host = env.get("YUKI_MCP_HOST", "127.0.0.1").strip()
    try:
        port = int(env.get("YUKI_MCP_PORT", "8765"))
    except ValueError as exc:
        raise BridgeConfigError("YUKI_MCP_PORT must be an integer.") from exc
    if not 1 <= port <= 65535:
        raise BridgeConfigError("YUKI_MCP_PORT must be between 1 and 65535.")
    if host not in {"127.0.0.1", "::1", "localhost"} and env.get("YUKI_MCP_ALLOW_REMOTE") != "true":
        raise BridgeConfigError("Remote binding requires YUKI_MCP_ALLOW_REMOTE=true.")
    if host not in {"127.0.0.1", "::1", "localhost"} and len(token) < 40:
        raise BridgeConfigError("Remote deployments require a bearer token of at least 40 characters.")
    return {"token": token, "host": host, "port": port}


def _jsonrpc_error(request_id, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def _tool_result(text: str, *, is_error: bool = False) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": text}],
        "isError": is_error,
    }


def _handle_rpc(message: Any, agent: YukiAgent, providers: ProviderManager) -> dict[str, Any] | None:
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
        return _jsonrpc_error(None, -32600, "Invalid JSON-RPC request.")
    method = message.get("method")
    request_id = message.get("id")
    if not isinstance(method, str) or not method:
        return _jsonrpc_error(request_id, -32600, "Method is required.")
    if "id" in message and not (
        request_id is None
        or (isinstance(request_id, str) and len(request_id) <= 128)
        or (isinstance(request_id, int) and not isinstance(request_id, bool))
    ):
        return _jsonrpc_error(None, -32600, "Invalid JSON-RPC request id.")
    if method == "notifications/initialized":
        return None
    if method == "initialize":
        params = message.get("params") or {}
        requested = params.get("protocolVersion") if isinstance(params, dict) else None
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "protocolVersion": requested if requested == PROTOCOL_VERSION else PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "yuki-chatgpt-bridge", "version": "0.1.0"},
                "instructions": "Yuki exposes only read-only status and text chat. Chat prompts are sent to Yuki's configured provider. No tools, files, or shell commands are exposed.",
            },
        }
    if method == "ping":
        return {"jsonrpc": "2.0", "id": request_id, "result": {}}
    if method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "tools": [
                    {
                        "name": "yuki_status",
                        "description": "Return Yuki runtime and provider readiness. Does not expose secrets or local paths.",
                        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
                    },
                    {
                        "name": "yuki_chat",
                        "description": "Send a text message to Yuki and return its actual configured-provider response. This does not execute skills or perform external actions.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {"message": {"type": "string", "minLength": 1, "maxLength": MAX_PROMPT_CHARS}},
                            "required": ["message"],
                            "additionalProperties": False,
                        },
                    },
                ]
            },
        }
    if method == "tools/call":
        params = message.get("params")
        if not isinstance(params, dict) or not isinstance(params.get("name"), str):
            return _jsonrpc_error(request_id, -32602, "Tool name is required.")
        name = params["name"]
        arguments = params.get("arguments", {})
        if not isinstance(arguments, dict):
            return _jsonrpc_error(request_id, -32602, "Tool arguments must be an object.")
        if name == "yuki_status":
            current = providers.current()
            status = agent.status()
            payload = {
                "runtime": "ready",
                "provider": current.name if current else "none",
                "provider_status": status,
                "capabilities": ["chat", "status"],
            }
            return {"jsonrpc": "2.0", "id": request_id, "result": _tool_result(json.dumps(payload))}
        if name == "yuki_chat":
            prompt = arguments.get("message")
            if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > MAX_PROMPT_CHARS:
                return {"jsonrpc": "2.0", "id": request_id, "result": _tool_result(
                    "message must be non-empty text of at most 12000 characters.", is_error=True)}
            try:
                response = agent.ask(prompt.strip())
            except Exception:
                response = "Yuki could not complete the request."
            return {"jsonrpc": "2.0", "id": request_id, "result": _tool_result(response)}
        return {"jsonrpc": "2.0", "id": request_id, "result": _tool_result("Unknown tool.", is_error=True)}
    if method.startswith("notifications/"):
        return None
    return _jsonrpc_error(request_id, -32601, "Method not found.")


def create_handler(agent=None, providers=None, *, token=None):
    """Create an isolated handler class for deterministic tests."""
    manager = providers or ProviderManager()
    selected_agent = agent or YukiAgent(provider=manager.create_selected_adapter())
    bearer_token = token if token is not None else os.environ.get("YUKI_MCP_BEARER_TOKEN", "")

    class MCPHandler(BaseHTTPRequestHandler):
        server_version = "YukiMCP/0.1"
        sys_version = ""

        def log_message(self, format, *args):
            # Avoid logging authorization headers, prompts, or response content.
            return

        def _send_json(self, status, body):
            encoded = json.dumps(body, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(encoded)

        def do_GET(self):
            if urlsplit(self.path).path == "/health":
                self._send_json(200, {"service": "yuki-chatgpt-bridge", "status": "ok"})
            else:
                self._send_json(404, {"error": "not found"})

        def do_POST(self):
            self.connection.settimeout(REQUEST_READ_TIMEOUT_SECONDS)
            if urlsplit(self.path).path != "/mcp":
                self._send_json(404, {"error": "not found"})
                return
            authorization = self.headers.get("Authorization", "")
            expected = "Bearer " + bearer_token
            if not bearer_token or not hmac.compare_digest(authorization, expected):
                self._send_json(401, {"error": "unauthorized"})
                return
            content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
            if content_type != "application/json":
                self._send_json(415, {"error": "content type must be application/json"})
                return
            accept = self.headers.get("Accept", "")
            accepted_types = {item.split(";", 1)[0].strip().lower() for item in accept.split(",")}
            if not {"application/json", "text/event-stream"}.issubset(accepted_types):
                self._send_json(406, {"error": "Accept must include application/json and text/event-stream"})
                return
            peer = self.client_address[0]
            if _rate_limit_exceeded(peer):
                self._send_json(429, {"error": "rate limit exceeded"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                self._send_json(400, {"error": "invalid content length"})
                return
            if length < 1 or length > MAX_BODY_BYTES:
                self._send_json(413, {"error": "request body size is invalid"})
                return
            try:
                body = json.loads(self.rfile.read(length).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                self._send_json(400, {"error": "invalid JSON"})
                return
            if isinstance(body, list):
                if not body or len(body) > MAX_BATCH_REQUESTS:
                    self._send_json(400, {"error": "batch must contain between 1 and 10 requests"})
                    return
                responses = [_handle_rpc(item, selected_agent, manager) for item in body]
                responses = [item for item in responses if item is not None]
                if not responses:
                    self.send_response(202)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                self._send_json(200, responses)
                return
            response = _handle_rpc(body, selected_agent, manager)
            if response is None:
                self.send_response(202)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            self._send_json(200, response)

    return MCPHandler


def main() -> None:
    config = _load_config()
    server = ThreadingHTTPServer((config["host"], config["port"]), create_handler(token=config["token"]))
    server.daemon_threads = True
    print(f"Yuki MCP bridge listening on {config['host']}:{config['port']}; authenticated /mcp endpoint enabled.")
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
