"""Restricted MCP Streamable HTTP client with pinned DNS, bounded RPC, and fail-closed approval."""
from __future__ import annotations

import http.client
import ipaddress
import json
import socket
import ssl
import threading
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from core.prompt_guard import scan_untrusted_text


class MCPError(RuntimeError):
    """Safe-to-display MCP client error."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """HTTPS connection that dials validated IPs while verifying the original hostname."""

    def __init__(self, host, *, pinned_addresses, **kwargs):
        super().__init__(host, **kwargs)
        self._pinned_addresses = tuple(pinned_addresses)

    def connect(self):
        last_error = None
        for address in self._pinned_addresses:
            raw = None
            try:
                raw = socket.create_connection((address, self.port), self.timeout, self.source_address)
                self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
                return
            except OSError as exc:
                last_error = exc
                if raw is not None:
                    raw.close()
        raise OSError("Could not connect to any validated MCP address.") from last_error


class MCPHTTPClient:
    MAX_RESPONSE_BYTES = 1_000_000
    MAX_ARGUMENT_BYTES = 64_000
    MAX_TOOLS = 200
    PROTOCOL_VERSION = "2025-03-26"

    def __init__(self, endpoint, *, allowed_hosts=(), allowed_tools=(), approver=None, timeout=10):
        self.endpoint, self._host, self._addresses = self._validate_endpoint(endpoint, allowed_hosts)
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 1 <= timeout <= 60:
            raise MCPError("MCP timeout must be between 1 and 60 seconds.")
        self.allowed_tools = frozenset(
            name for name in allowed_tools
            if isinstance(name, str) and 0 < len(name) <= 128
        )
        self.approver = approver
        self.timeout = float(timeout)
        self.session_id = None
        self._request_id = 0
        self._lock = threading.RLock()

        addresses = self._addresses

        class PinnedHandler(urllib.request.HTTPSHandler):
            def https_open(self, req):
                def connection_factory(host, **kwargs):
                    return _PinnedHTTPSConnection(host, pinned_addresses=addresses, **kwargs)
                return self.do_open(connection_factory, req)

        # Do not use environment proxy settings: a proxy would bypass the pinned-IP connection.
        self._opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), _NoRedirect(), PinnedHandler(context=ssl.create_default_context())
        )

    @staticmethod
    def _validate_endpoint(endpoint, allowed_hosts):
        if not isinstance(endpoint, str) or len(endpoint) > 2048:
            raise MCPError("MCP endpoint must be a bounded URL.")
        try:
            parts = urlsplit(endpoint.strip())
            host = (parts.hostname or "").lower().rstrip(".")
            port = parts.port
        except ValueError as exc:
            raise MCPError("MCP endpoint URL is invalid.") from exc
        allowed = frozenset(
            str(item).lower().rstrip(".") for item in allowed_hosts
            if isinstance(item, str) and item.strip()
        )
        if (parts.scheme != "https" or not host or parts.username or parts.password
                or port not in (None, 443) or parts.fragment):
            raise MCPError("Only HTTPS MCP endpoints on the default port are accepted.")
        if not allowed or host not in allowed:
            raise MCPError("MCP host is not on the explicit allow-list.")
        try:
            infos = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
            addresses = tuple(dict.fromkeys(item[4][0] for item in infos))
            if not addresses or any(not ipaddress.ip_address(addr).is_global for addr in addresses):
                raise MCPError("Non-public MCP destinations are blocked.")
        except (OSError, ValueError) as exc:
            raise MCPError("MCP host could not be resolved to safe public addresses.") from exc
        # The resolved addresses are pinned for every connection to prevent DNS rebinding between
        # validation and connect. TLS still verifies the certificate against the URL hostname.
        return endpoint.strip(), host, addresses

    def _rpc(self, method, params=None, *, notification=False):
        # MCP session IDs and request IDs are mutable; serialize calls to prevent response mix-ups.
        with self._lock:
            self._request_id += 1
            request_id = self._request_id
            payload = {"jsonrpc": "2.0", "method": method}
            if not notification:
                payload["id"] = request_id
            if params is not None:
                payload["params"] = params
            headers = {"Content-Type": "application/json", "Accept": "application/json"}
            if self.session_id:
                headers["Mcp-Session-Id"] = self.session_id
            request = urllib.request.Request(
                self.endpoint, data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
                headers=headers, method="POST"
            )
            try:
                with self._opener.open(request, timeout=self.timeout) as response:
                    self.session_id = response.headers.get("Mcp-Session-Id", self.session_id)
                    body = response.read(self.MAX_RESPONSE_BYTES + 1)
                    if len(body) > self.MAX_RESPONSE_BYTES:
                        raise MCPError("MCP response exceeds the 1 MB limit.")
                    if not body and notification:
                        return None
                    content_type = response.headers.get("Content-Type", "").lower()
                    if "application/json" not in content_type:
                        raise MCPError("This client supports JSON MCP responses, not SSE-only responses.")
                    result = json.loads(body.decode("utf-8"))
            except MCPError:
                raise
            except urllib.error.HTTPError as exc:
                raise MCPError(f"MCP HTTP request failed ({exc.code}); redirects are disabled.") from exc
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                raise MCPError("MCP server was unreachable or timed out.") from exc
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise MCPError("MCP server returned invalid JSON.") from exc
            if not isinstance(result, dict) or result.get("jsonrpc") != "2.0":
                raise MCPError("MCP server returned an invalid JSON-RPC envelope.")
            if notification and "id" in result:
                raise MCPError("MCP notification unexpectedly returned a request ID.")
            if not notification and result.get("id") != request_id:
                raise MCPError("MCP response ID did not match the request.")
            if "error" in result:
                raise MCPError("MCP server returned a JSON-RPC error.")
            if "result" not in result and not notification:
                raise MCPError("MCP response is missing its result.")
            return result.get("result")

    def initialize(self):
        result = self._rpc("initialize", {
            "protocolVersion": self.PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "Yuki", "version": "0.2"},
        })
        if not isinstance(result, dict):
            raise MCPError("MCP initialize response is invalid.")
        self._rpc("notifications/initialized", notification=True)
        return {
            "server_info": result.get("serverInfo", {}),
            "protocol_version": result.get("protocolVersion", self.PROTOCOL_VERSION),
        }

    def list_tools(self):
        result = self._rpc("tools/list", {})
        tools = result.get("tools", []) if isinstance(result, dict) else None
        if not isinstance(tools, list):
            raise MCPError("MCP tools/list response is invalid.")
        safe = []
        for tool in tools[:self.MAX_TOOLS]:
            if not isinstance(tool, dict) or not isinstance(tool.get("name"), str):
                continue
            schema = tool.get("inputSchema", {})
            if not isinstance(schema, dict):
                continue
            safe.append({
                "name": tool["name"][:128],
                "description": str(tool.get("description", ""))[:500],
                "input_schema": schema,
            })
        return safe

    def call_tool(self, name, arguments):
        if not isinstance(name, str) or name not in self.allowed_tools:
            raise MCPError("MCP tool is not explicitly allow-listed.")
        if not isinstance(arguments, dict):
            raise MCPError("MCP tool arguments must be an object.")
        try:
            encoded_arguments = json.dumps(arguments, ensure_ascii=False).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise MCPError("MCP tool arguments must be JSON-serializable.") from exc
        if len(encoded_arguments) > self.MAX_ARGUMENT_BYTES:
            raise MCPError("MCP tool arguments exceed the 64 KB limit.")
        if self.approver is None:
            raise MCPError("MCP tool call was not explicitly approved.")
        try:
            approved = self.approver(name, arguments)
        except Exception as exc:
            raise MCPError("MCP approval check failed closed.") from exc
        # This client is intentionally synchronous; coroutine/future callbacks are never truthy approvals.
        if approved is not True:
            if hasattr(approved, "close") and callable(approved.close):
                approved.close()
            raise MCPError("MCP tool call was not explicitly approved.")
        result = self._rpc("tools/call", {"name": name, "arguments": arguments})
        serialized = json.dumps(result, ensure_ascii=False)
        scan = scan_untrusted_text(serialized)
        return {"result": result, "untrusted": True, "prompt_injection_scan": scan}
