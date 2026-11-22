"""Bridge Yuki's approved read-only workspace registry into code execution.

Generated code never receives filesystem access or a tool implementation. Every
RPC is resolved against the immutable request manifest and dispatched through
AuthorizedReadOnlyTools, which rechecks the authenticated principal, permission,
path policy, and mandatory audit boundary.
"""
from __future__ import annotations

from typing import Callable, Mapping, Any

from automation.authorized_read_tools import (
    AuthorizedReadOnlyTools,
    ToolPrincipal,
)
from core.code_execution import CodeExecutionContractError, CodeExecutionRequest


PrincipalResolver = Callable[[CodeExecutionRequest], ToolPrincipal]


def build_authenticated_tool_principal_resolver(
    *,
    authenticated_context: Callable[[CodeExecutionRequest], bool],
    permissions_for_actor: Callable[[str], frozenset[str]],
) -> PrincipalResolver:
    """Compose live session verification with Yuki's trusted permission policy.

    permissions_for_actor must query the authoritative application policy for
    the authenticated actor. It must not accept permissions from request data.
    """
    if not callable(authenticated_context) or not callable(permissions_for_actor):
        raise TypeError("live authentication and permission resolvers are required")

    def resolve(request: CodeExecutionRequest) -> ToolPrincipal:
        request.validate()
        try:
            active = authenticated_context(request)
        except Exception as exc:
            raise CodeExecutionContractError("authenticated session could not be verified") from exc
        if active is not True:
            raise CodeExecutionContractError("authenticated actor or session is no longer active")
        try:
            permissions = permissions_for_actor(request.actor_id)
        except Exception as exc:
            raise CodeExecutionContractError("current tool permissions could not be resolved") from exc
        if not isinstance(permissions, frozenset) or any(
            not isinstance(item, str) or not item or len(item) > 128 for item in permissions
        ):
            raise CodeExecutionContractError("trusted tool permissions are invalid")
        return ToolPrincipal(request.actor_id, permissions)

    return resolve


class AuthenticatedReadToolDispatcher:
    """Request-bound dispatcher for Yuki's existing read-only tool registry.

    principal_resolver must resolve the request's actor and session fingerprint
    against the live authenticated application context and return permissions
    derived from trusted Yuki policy, never from generated-code arguments.
    AuthorizedReadOnlyTools performs a second trust check before every call.
    """

    def __init__(
        self,
        tools: AuthorizedReadOnlyTools,
        *,
        principal_resolver: PrincipalResolver,
    ) -> None:
        if not isinstance(tools, AuthorizedReadOnlyTools):
            raise TypeError("trusted AuthorizedReadOnlyTools registry is required")
        if not callable(principal_resolver):
            raise TypeError("live authenticated principal resolver is required")
        self._tools = tools
        self._principal_resolver = principal_resolver

    @property
    def tool_names(self) -> tuple[str, ...]:
        """Names available to a request manifest; all are read-only."""
        return self._tools.tool_names

    def bind(self, request: CodeExecutionRequest):
        """Return the exact host RPC callback expected by CodeExecutionService."""
        request.validate()

        def dispatch(tool_name: str, arguments: Mapping[str, Any]) -> Mapping[str, object]:
            return self.dispatch(request, tool_name, arguments)

        return dispatch

    def dispatch(
        self,
        request: CodeExecutionRequest,
        tool_name: str,
        arguments: Mapping[str, Any],
    ) -> Mapping[str, object]:
        request.validate()
        if not isinstance(tool_name, str) or tool_name not in request.tool_names:
            raise CodeExecutionContractError(
                "tool is not present in the exact execution request manifest"
            )
        if tool_name not in self._tools.tool_names:
            raise CodeExecutionContractError("tool is not registered by Yuki")
        if not isinstance(arguments, Mapping):
            raise CodeExecutionContractError("tool arguments must be a mapping")
        try:
            principal = self._principal_resolver(request)
        except Exception as exc:
            raise CodeExecutionContractError(
                "authenticated principal could not be resolved for tool dispatch"
            ) from exc
        if not isinstance(principal, ToolPrincipal):
            raise CodeExecutionContractError(
                "principal resolver did not return a trusted ToolPrincipal"
            )
        if principal.actor_id != request.actor_id:
            raise CodeExecutionContractError(
                "resolved principal does not own the execution request"
            )
        result = self._tools.execute(
            principal=principal,
            tool_name=tool_name,
            arguments=arguments,
        )
        if not isinstance(result.data, dict):
            raise CodeExecutionContractError("read-only tool returned an invalid result")
        return result.data
