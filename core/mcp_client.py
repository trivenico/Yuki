"""Restricted MCP Streamable HTTP client. No stdio subprocesses or implicit tool trust."""
from __future__ import annotations
import http.client
import ipaddress
import json
import socket
import ssl
from urllib.parse import urlsplit
from core.prompt_guard import scan_untrusted_text


class MCPError(RuntimeError):
    pass


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """HTTPS transport pinned to a previously validated IP address."""

    def __init__(self, host, address, *, timeout):
        super().__init__(host, port=443, timeout=timeout, context=ssl.create_default_context())
        self._pinned_address = address

    def connect(self):
        raw = socket.create_connection((self._pinned_address, self.port), self.timeout)
        try:
            self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
        except Exception:
            raw.close()
            raise


class MCPHTTPClient:
    MAX_RESPONSE_BYTES = 1_000_000
    PROTOCOL_VERSION = "2025-03-26"

    def __init__(self, endpoint, *, allowed_hosts=(), allowed_tools=(), approver=None, timeout=10):
        self.endpoint = self._validate_endpoint(endpoint, allowed_hosts)
        parts = urlsplit(self.endpoint)
        self._host = parts.hostname.lower().rstrip(".")
        self._request_target = parts.path or "/"
        if parts.query:
            self._request_target += "?" + parts.query
        # Resolve once for the actual transport, reject any non-public answer, and
        # connect to this exact IP while retaining hostname-based TLS verification.
        try:
            addresses = sorted({item[4][0] for item in socket.getaddrinfo(
                self._host, 443, type=socket.SOCK_STREAM
            )})
            if not addresses or any(not ipaddress.ip_address(addr).is_global for addr in addresses):
                raise MCPError("Non-public MCP destinations are blocked.")
            self._pinned_address = addresses[0]
        except OSError as exc:
            raise MCPError("MCP host could not be resolved.") from exc
        self.allowed_tools = frozenset(allowed_tools)
        self.approver = approver
        self.timeout = timeout
        self.session_id = None
        self._request_id = 0

    @staticmethod
    def _validate_endpoint(endpoint, allowed_hosts):
        if not isinstance(endpoint, str) or len(endpoint) > 2048:
            raise MCPError("MCP endpoint must be a bounded URL.")
        try:
            parts = urlsplit(endpoint.strip())
            host = (parts.hostname or "").lower().rstrip(".")
            port = parts.port
        except ValueError as exc:
            raise MCPError("MCP endpoint URL is malformed.") from exc
        allowed = frozenset(str(h).lower().rstrip(".") for h in allowed_hosts)
        if parts.scheme != "https" or not host or parts.username or parts.password or port not in (None, 443):
            raise MCPError("Only HTTPS MCP endpoints on the default port are accepted.")
        if not allowed or host not in allowed:
            raise MCPError("MCP host is not on the explicit allow-list.")
        try:
            addresses = {item[4][0] for item in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)}
            if not addresses or any(not ipaddress.ip_address(addr).is_global for addr in addresses):
                raise MCPError("Non-public MCP destinations are blocked.")
        except OSError as exc:
            raise MCPError("MCP host could not be resolved.") from exc
        return endpoint.strip()

    def _rpc(self, method, params=None, *, notification=False):
        self._request_id += 1
        payload = {"jsonrpc": "2.0", "method": method}
        if not notification:
            payload["id"] = self._request_id
        if params is not None:
            payload["params"] = params
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        connection = _PinnedHTTPSConnection(
            self._host, self._pinned_address, timeout=self.timeout
        )
        try:
            connection.request(
                "POST", self._request_target, body=json.dumps(payload).encode("utf-8"),
                headers={**headers, "Host": self._host},
            )
            response = connection.getresponse()
            self.session_id = response.getheader("Mcp-Session-Id", self.session_id)
            body = response.read(self.MAX_RESPONSE_BYTES + 1)
            if len(body) > self.MAX_RESPONSE_BYTES:
                raise MCPError("MCP response exceeds the 1 MB limit.")
            if response.status < 200 or response.status >= 300:
                # Never follow redirects; they could bypass the endpoint allow-list.
                raise MCPError(f"MCP HTTP request failed ({response.status}); redirects are disabled.")
            if not body and notification:
                return None
            if "application/json" not in (response.getheader("Content-Type", "")).lower():
                raise MCPError("This client currently supports JSON MCP responses, not SSE-only responses.")
            try:
                result = json.loads(body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise MCPError("MCP server returned invalid JSON.") from exc
        except (OSError, TimeoutError, http.client.HTTPException, ssl.SSLError) as exc:
            raise MCPError("MCP server was unreachable or timed out.") from exc
        finally:
            connection.close()
        if not isinstance(result, dict) or result.get("jsonrpc") != "2.0":
            raise MCPError("MCP server returned an invalid JSON-RPC envelope.")
        if "error" in result:
            raise MCPError("MCP server returned a JSON-RPC error.")
        return result.get("result")

    def initialize(self):
        result = self._rpc("initialize", {
            "protocolVersion": self.PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "Yuki", "version": "0.1"},
        })
        if not isinstance(result, dict):
            raise MCPError("MCP initialize response is invalid.")
        self._rpc("notifications/initialized", notification=True)
        return {"server_info": result.get("serverInfo", {}),
                "protocol_version": result.get("protocolVersion", self.PROTOCOL_VERSION)}

    def list_tools(self):
        result = self._rpc("tools/list", {})
        tools = result.get("tools", []) if isinstance(result, dict) else None
        if not isinstance(tools, list):
            raise MCPError("MCP tools/list response is invalid.")
        safe = []
        for tool in tools[:200]:
            if not isinstance(tool, dict) or not isinstance(tool.get("name"), str):
                continue
            safe.append({"name": tool["name"][:128], "description": str(tool.get("description", ""))[:500],
                         "input_schema": tool.get("inputSchema", {})})
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
        if len(encoded_arguments) > 64000:
            raise MCPError("MCP tool arguments exceed the 64 KB limit.")
        if self.approver is None or self.approver(name, arguments) is not True:
            raise MCPError("MCP tool call was not explicitly approved.")
        result = self._rpc("tools/call", {"name": name, "arguments": arguments})
        serialized = json.dumps(result, ensure_ascii=False)
        scan = scan_untrusted_text(serialized)
        return {"result": result, "untrusted": True, "prompt_injection_scan": scan}
