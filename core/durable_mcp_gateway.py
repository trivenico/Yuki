"""Durable approval gateway for side-effect-capable MCP tool calls.

MCP servers are external executors. Callers should use this gateway instead of calling
MCPHTTPClient.call_tool directly when a durable coordinator is available. The client's
own approver remains enabled as a second explicit-consent check.
"""
from __future__ import annotations

from typing import Any

from core.durable_operation_runtime import DurableOperationRuntime
from core.mcp_client import MCPHTTPClient


class DurableMCPGateway:
    """Apply durable approval, audit, and execution-ledger policy to MCP calls."""

    def __init__(
        self,
        *,
        client: MCPHTTPClient,
        coordinator,
        actor: str,
        session_id: str,
        confirm,
    ) -> None:
        if not isinstance(client, MCPHTTPClient):
            raise TypeError("A restricted MCPHTTPClient is required.")
        if not callable(getattr(client, "call_tool", None)):
            raise ValueError("MCP client does not support tool calls.")
        self.client = client
        self.runtime = DurableOperationRuntime(
            coordinator=coordinator,
            operations={"mcp_call": self._invoke},
            actor=actor,
            session_id=session_id,
            confirm=confirm,
        )

    def call_tool(self, name: str, arguments: dict[str, Any]):
        """Execute an allow-listed MCP tool only after durable approval."""
        if not isinstance(name, str) or not name.strip():
            raise ValueError("MCP tool name must be a non-empty string.")
        if not isinstance(arguments, dict):
            raise ValueError("MCP tool arguments must be an object.")
        return self.runtime.execute("mcp_call", {"name": name, "arguments": arguments})

    def _invoke(self, name: str, arguments: dict[str, Any]):
        # The MCP client still enforces its endpoint/tool allow-lists and its own
        # explicit approver. The durable wrapper does not weaken those checks.
        return self.client.call_tool(name, arguments)
