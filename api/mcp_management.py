"""Authenticated, read-only MCP discovery and policy management endpoints."""
from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Mapping

from core.mcp_runtime import MCPRuntimeService
from core.oauth_connection_registry import SQLiteOAuthConnectionRegistry

_PREFIX = "/v1/integrations/mcp"
_IDENTIFIER = r"[A-Za-z0-9][A-Za-z0-9_.:@+-]{0,127}"


class AuthenticatedMCPManagementAPI:
    """Expose inert tool metadata and owner-scoped policy snapshots only."""

    def __init__(self, app, auth_service, oauth_registry: SQLiteOAuthConnectionRegistry,
                 mcp_runtime: MCPRuntimeService):
        if not isinstance(oauth_registry, SQLiteOAuthConnectionRegistry):
            raise TypeError("trusted_oauth_connection_registry_required")
        if not isinstance(mcp_runtime, MCPRuntimeService):
            raise TypeError("trusted_mcp_runtime_service_required")
        self.app = app
        self.auth = auth_service
        self.oauth_registry = oauth_registry
        self.mcp_runtime = mcp_runtime

    def __getattr__(self, name):
        return getattr(self.app, name)

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "/")
        if not path.startswith(_PREFIX + "/"):
            await self.app(scope, receive, send)
            return
        if scope.get("method", "").upper() != "GET":
            return await self._respond(send, 405, {"error": "method_not_allowed"})
        match = re.fullmatch(
            re.escape(_PREFIX) + rf"/({_IDENTIFIER})/({_IDENTIFIER})/(tools|policies)",
            path,
        )
        if match is None:
            return await self._respond(send, 404, {"error": "not_found"})
        try:
            token = self._session_token(scope)
            if token is None:
                return await self._respond(send, 401, {"error": "authentication_required"})
            user = await asyncio.to_thread(self.auth.resolve_session, token)
            if user is None:
                return await self._respond(send, 401, {"error": "authentication_required"})
            provider_id, account_id, resource = match.groups()
            if not self.mcp_runtime.has_provider(provider_id):
                return await self._respond(send, 404, {"error": "provider_not_configured"})
            connection = await asyncio.to_thread(
                self.oauth_registry.get, owner_id=user.user_id,
                provider_id=provider_id, account_id=account_id,
            )
            if connection is None:
                return await self._respond(send, 404, {"error": "connection_not_found"})

            if resource == "tools":
                snapshot = await asyncio.to_thread(
                    self.mcp_runtime.discover_tools,
                    owner_id=user.user_id,
                    provider_id=provider_id,
                    account_id=account_id,
                    granted_scopes=connection.scopes,
                )
                return await self._respond(send, 200, {
                    "provider_id": snapshot.provider_id,
                    "account_id": snapshot.account_id,
                    "server_info": self._plain_json(snapshot.server_info),
                    "protocol_version": snapshot.protocol_version,
                    "tools_trust": "untrusted_remote_server",
                    "execution_enabled": False,
                    "tools": [{
                        "name": tool.name,
                        "description": tool.description,
                        "input_schema": self._plain_json(tool.input_schema),
                        "metadata_trust": "untrusted_remote_server",
                        "read_only": False,
                        "execution_enabled": False,
                    } for tool in snapshot.tools],
                })

            registry = await asyncio.to_thread(
                self.mcp_runtime.list_policies,
                owner_id=user.user_id,
                provider_id=provider_id,
                account_id=account_id,
            )
            return await self._respond(send, 200, {
                "provider_id": provider_id,
                "account_id": account_id,
                "revision": registry.revision,
                "policies": [{
                    "tool_name": policy.tool_name,
                    "risk": policy.risk.value,
                    "required_scopes": list(policy.required_scopes),
                    "version": policy.version,
                    "enabled": policy.enabled,
                } for policy in registry.policies],
            })
        except ValueError as exc:
            code = str(exc)
            if code == "mcp_provider_not_configured":
                return await self._respond(send, 404, {"error": "provider_not_configured"})
            if code == "required_oauth_scope_missing":
                return await self._respond(send, 403, {"error": "required_oauth_scope_missing"})
            return await self._respond(send, 400, {"error": "invalid_request"})
        except Exception:
            return await self._respond(send, 502, {"error": "mcp_management_unavailable"})

    @staticmethod
    def _plain_json(value):
        if isinstance(value, Mapping):
            return {key: AuthenticatedMCPManagementAPI._plain_json(item)
                    for key, item in value.items()}
        if isinstance(value, (tuple, list)):
            return [AuthenticatedMCPManagementAPI._plain_json(item) for item in value]
        if value is None or isinstance(value, (str, bool, int, float)):
            return value
        raise ValueError("mcp_metadata_not_json")

    @staticmethod
    def _session_token(scope):
        values = [
            value for key, value in scope.get("headers", [])
            if isinstance(key, bytes) and key.lower() == b"authorization"
            and isinstance(value, bytes)
        ]
        if len(values) != 1:
            return None
        raw = values[0].decode("latin-1")
        scheme, separator, token = raw.partition(" ")
        if not separator or scheme.lower() != "bearer" or not 20 <= len(token) <= 256:
            return None
        return token

    @staticmethod
    async def _respond(send, status, payload):
        body = json.dumps(
            payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False,
        ).encode("utf-8")
        await send({"type": "http.response.start", "status": status, "headers": [
            (b"content-type", b"application/json; charset=utf-8"),
            (b"content-length", str(len(body)).encode("ascii")),
            (b"cache-control", b"no-store"),
            (b"x-content-type-options", b"nosniff"),
        ]})
        await send({"type": "http.response.body", "body": body})
