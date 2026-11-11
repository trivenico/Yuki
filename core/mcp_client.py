"""Restricted MCP Streamable HTTP client. No stdio subprocesses or implicit tool trust."""
from __future__ import annotations

import json
import re
import uuid
from urllib.parse import urlsplit

from core.mcp_pinned_transport import MCPPinnedTransportError, PinnedMCPHTTPTransport
from core.prompt_guard import scan_untrusted_text


class MCPError(RuntimeError):
    pass


class MCPRemoteError(MCPError):
    """The remote server returned an explicit JSON-RPC error response."""


class MCPHTTPClient:
    MAX_RESPONSE_BYTES = 1_000_000
    PROTOCOL_VERSION = "2025-03-26"
    _BEARER_TOKEN = re.compile(r"^[A-Za-z0-9._~+/-]+=*$")

    def __init__(self, endpoint, *, allowed_hosts=(), allowed_tools=(), approver=None,
                 access_token_provider=None, timeout=10, http_transport=None,
                 execution_audit=None):
        self.allowed_hosts = frozenset(str(host).lower().rstrip(".") for host in allowed_hosts)
        self.endpoint = self._validate_endpoint(endpoint, self.allowed_hosts)
        self.allowed_tools = frozenset(allowed_tools)
        self.approver = approver
        if access_token_provider is not None and not callable(access_token_provider):
            raise TypeError("MCP access token provider must be callable.")
        self._access_token_provider = access_token_provider
        if execution_audit is not None and not callable(execution_audit):
            raise TypeError("MCP execution audit must be callable.")
        self._execution_audit = execution_audit
        self.timeout = timeout
        if http_transport is not None and not callable(getattr(http_transport, "post_json", None)):
            raise TypeError("MCP HTTP transport must provide post_json.")
        self._http = http_transport or PinnedMCPHTTPTransport(allowed_hosts=self.allowed_hosts, timeout=timeout)
        self.session_id = None
        self._initialized = False
        self._request_id = 0

    @staticmethod
    def _validate_endpoint(endpoint, allowed_hosts):
        if not isinstance(endpoint, str) or not 1 <= len(endpoint) <= 2048:
            raise MCPError("MCP endpoint must be a bounded URL.")
        try:
            parts = urlsplit(endpoint)
            port = parts.port
        except ValueError:
            raise MCPError("MCP endpoint is invalid.") from None
        host = (parts.hostname or "").lower().rstrip(".")
        if (parts.scheme != "https" or not host or parts.username is not None
                or parts.password is not None or port not in (None, 443)
                or parts.fragment or host not in allowed_hosts):
            raise MCPError("MCP endpoint is not trusted.")
        return endpoint

    def _authorization_header(self):
        if self._access_token_provider is None:
            return None
        try:
            token = self._access_token_provider()
        except Exception:
            raise MCPError("MCP authorization credentials are unavailable.") from None
        if not isinstance(token, str) or not token or len(token) > 8192 or not self._BEARER_TOKEN.fullmatch(token):
            raise MCPError("MCP authorization credentials are invalid.")
        return "Bearer " + token

    def _rpc(self, method, params=None, *, notification=False):
        self._request_id += 1
        payload = {"jsonrpc": "2.0", "method": method}
        if not notification:
            payload["id"] = self._request_id
        if params is not None:
            payload["params"] = params
        headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        authorization = self._authorization_header()
        if authorization is not None:
            headers["Authorization"] = authorization
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        if self._initialized:
            headers["MCP-Protocol-Version"] = self.PROTOCOL_VERSION
        try:
            status, response_headers, body = self._http.post_json(self.endpoint, payload=payload, headers=headers)
        except MCPPinnedTransportError:
            raise MCPError("MCP server was unreachable or rejected the request.") from None
        self.session_id = response_headers.get("mcp-session-id", self.session_id)
        if not body and notification and status in (202, 204):
            return None
        content_type = response_headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if content_type != "application/json" and not content_type.endswith("+json"):
            raise MCPError("MCP server returned an unsupported response content type.")
        try:
            result = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise MCPError("MCP server returned invalid JSON.") from None
        if not isinstance(result, dict) or result.get("jsonrpc") != "2.0":
            raise MCPError("MCP server returned an invalid JSON-RPC envelope.")
        if not notification and result.get("id") != self._request_id:
            raise MCPError("MCP server returned a mismatched response identifier.")
        if "error" in result:
            raise MCPRemoteError("MCP server returned a JSON-RPC error.")
        return result.get("result")

    def initialize(self):
        result = self._rpc("initialize", {
            "protocolVersion": self.PROTOCOL_VERSION, "capabilities": {},
            "clientInfo": {"name": "Yuki", "version": "0.1"},
        })
        if not isinstance(result, dict):
            raise MCPError("MCP initialize response is invalid.")
        self._initialized = True
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

    def _record_execution(self, event, invocation_id, tool_name):
        if self._execution_audit is not None:
            self._execution_audit(event, invocation_id, tool_name)

    def call_tool(self, name, arguments):
        if not isinstance(name, str) or name not in self.allowed_tools:
            raise MCPError("MCP tool is not explicitly allow-listed.")
        if not isinstance(arguments, dict):
            raise MCPError("MCP tool arguments must be an object.")
        try:
            encoded_arguments = json.dumps(arguments, ensure_ascii=False, allow_nan=False).encode("utf-8")
        except (TypeError, ValueError, UnicodeEncodeError):
            raise MCPError("MCP tool arguments must be JSON-serializable.") from None
        if len(encoded_arguments) > 64000:
            raise MCPError("MCP tool arguments exceed the 64 KB limit.")
        if self.approver is None or self.approver(name, arguments) is not True:
            raise MCPError("MCP tool call was not explicitly approved.")

        invocation_id = uuid.uuid4().hex
        try:
            self._record_execution("execution_started", invocation_id, name)
        except Exception:
            raise MCPError("MCP execution audit is unavailable; call was not dispatched.") from None

        try:
            result = self._rpc("tools/call", {"name": name, "arguments": arguments})
        except MCPRemoteError:
            try:
                self._record_execution("execution_failed", invocation_id, name)
            except Exception:
                pass
            raise
        except Exception:
            try:
                self._record_execution("outcome_unknown", invocation_id, name)
            except Exception:
                pass
            raise

        event = "execution_failed" if isinstance(result, dict) and result.get("isError") is True else "executed"
        try:
            self._record_execution(event, invocation_id, name)
        except Exception:
            raise MCPError("MCP result received but durable audit confirmation failed.") from None

        serialized = json.dumps(result, ensure_ascii=False)
        scan = scan_untrusted_text(serialized)
        return {"result": result, "untrusted": True, "prompt_injection_scan": scan}
