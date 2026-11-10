"""Central-policy execution boundary for the restricted MCP HTTP client.

MCP discovery is metadata only. This adapter requires a trusted request factory,
central policy evaluation, and the client's independent explicit-call approver.
It does not make remote MCP servers trustworthy or sandbox their behavior.
"""
from __future__ import annotations

from typing import Any, Callable

from core.tool_policy import Decision, PolicyRequest, argument_digest


class GuardedMCPRuntime:
    """Route MCP tool calls through Yuki's central policy before network I/O."""

    def __init__(
        self,
        client: Any,
        policy_engine: Any,
        policy_request_factory: Callable[[str, dict[str, Any]], PolicyRequest],
        *,
        approval_gateway: Any = None,
        audit: Callable[[str, str], None] | None = None,
    ) -> None:
        if not callable(getattr(client, "call_tool", None)):
            raise TypeError("client must provide call_tool(name, arguments)")
        if not callable(getattr(policy_engine, "evaluate", None)):
            raise TypeError("policy_engine must provide evaluate(request)")
        if not callable(policy_request_factory):
            raise TypeError("policy_request_factory must be callable")
        if approval_gateway is not None and not callable(
            getattr(approval_gateway, "authorize", None)
        ):
            raise TypeError("approval_gateway must provide authorize(request, approval_token=...)")
        if audit is not None and not callable(audit):
            raise TypeError("audit must be callable")
        self._client = client
        self._policy_engine = policy_engine
        self._request_factory = policy_request_factory
        self._approval_gateway = approval_gateway
        self._audit = audit

    def _record(self, tool_name: str, outcome: str) -> None:
        if self._audit is not None:
            try:
                self._audit("mcp:" + tool_name[:128], outcome)
            except Exception:
                # Audit sink outages must not change authorization semantics.
                pass

    def call_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        approval_token: str | None = None,
    ) -> dict[str, Any]:
        if not isinstance(name, str) or not name or len(name) > 128:
            self._record("invalid", "invalid_name")
            raise ValueError("MCP tool name is invalid")
        if not isinstance(arguments, dict):
            self._record(name, "invalid_arguments")
            raise ValueError("MCP tool arguments must be an object")

        try:
            request = self._request_factory(name, arguments)
            if not isinstance(request, PolicyRequest):
                raise ValueError("request factory returned an invalid policy request")
            if request.capability != "mcp.tool.invoke" or request.target != name:
                raise ValueError("policy request is not bound to this MCP capability and tool")
            if request.argument_sha256 != argument_digest(arguments):
                raise ValueError("policy request is not bound to the exact MCP arguments")

            policy_result = self._policy_engine.evaluate(request)
            policy_decision = getattr(policy_result, "decision", None)
            if policy_decision == Decision.DENY or policy_decision == Decision.QUARANTINE:
                self._record(name, "central_policy_" + policy_decision.value)
                raise PermissionError("MCP tool call blocked by central policy")
            if policy_decision == Decision.ALLOW:
                pass
            elif policy_decision == Decision.REQUIRE_APPROVAL:
                if self._approval_gateway is None:
                    self._record(name, "central_policy_approval_required")
                    raise PermissionError("MCP tool call requires an approval gateway")
                gateway_decision = self._approval_gateway.authorize(
                    request, approval_token=approval_token,
                )
                if getattr(gateway_decision, "allowed", False) is not True:
                    status = getattr(gateway_decision, "status", "denied")
                    self._record(name, "approval_" + str(status)[:64])
                    raise PermissionError("MCP tool call approval was not authorized")
            else:
                self._record(name, "central_policy_invalid_decision")
                raise PermissionError("MCP tool call blocked by invalid policy decision")

            # The MCP client independently enforces its HTTPS host/tool allow-lists
            # and explicit per-call approver. Central authorization cannot bypass it.
            result = self._client.call_tool(name, arguments)
            self._record(name, "completed")
            return result
        except PermissionError:
            raise
        except Exception:
            self._record(name, "policy_or_execution_error")
            raise
