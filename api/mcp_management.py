"""Authenticated, owner-scoped MCP discovery and policy controls."""
from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Mapping

from core.mcp_runtime import MCPRuntimeService
from core.mcp_tool_policy_store import MCPToolPolicyConflict
from core.oauth_connection_registry import SQLiteOAuthConnectionRegistry

_PREFIX = "/v1/integrations/mcp"
_IDENTIFIER = r"[A-Za-z0-9][A-Za-z0-9_.:@+-]{0,127}"
_TOOL = r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}"
_MAX_BODY = 8192


class AuthenticatedMCPManagementAPI:
    """Expose inert discovery, policy reads, and explicit owner policy changes."""

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
        method = scope.get("method", "").upper()
        base = re.fullmatch(
            re.escape(_PREFIX) + rf"/({_IDENTIFIER})/({_IDENTIFIER})/(tools|policies)",
            path,
        )
        mutation = re.fullmatch(
            re.escape(_PREFIX) + rf"/({_IDENTIFIER})/({_IDENTIFIER})/policies/({_TOOL})",
            path,
        )
        if method == "GET" and base is not None:
            return await self._read(scope, send, base.groups())
        if method in {"PUT", "DELETE"} and mutation is not None:
            return await self._mutate(scope, receive, send, mutation.groups(), method)
        if method not in {"GET", "PUT", "DELETE"}:
            return await self._respond(send, 405, {"error": "method_not_allowed"})
        return await self._respond(send, 404, {"error": "not_found"})

    async def _authenticate_connection(self, scope, send, provider_id, account_id):
        token = self._session_token(scope)
        if token is None:
            await self._respond(send, 401, {"error": "authentication_required"})
            return None
        user = await asyncio.to_thread(self.auth.resolve_session, token)
        if user is None:
            await self._respond(send, 401, {"error": "authentication_required"})
            return None
        if not self.mcp_runtime.has_provider(provider_id):
            await self._respond(send, 404, {"error": "provider_not_configured"})
            return None
        connection = await asyncio.to_thread(
            self.oauth_registry.get, owner_id=user.user_id,
            provider_id=provider_id, account_id=account_id,
        )
        if connection is None:
            await self._respond(send, 404, {"error": "connection_not_found"})
            return None
        return user, connection

    async def _read(self, scope, send, parts):
        provider_id, account_id, resource = parts
        try:
            authenticated = await self._authenticate_connection(
                scope, send, provider_id, account_id,
            )
            if authenticated is None:
                return
            user, connection = authenticated
            if resource == "tools":
                snapshot = await asyncio.to_thread(
                    self.mcp_runtime.discover_tools,
                    owner_id=user.user_id, provider_id=provider_id,
                    account_id=account_id, granted_scopes=connection.scopes,
                )
                return await self._respond(send, 200, {
                    "provider_id": snapshot.provider_id,
                    "account_id": snapshot.account_id,
                    "server_info": self._plain_json(snapshot.server_info),
                    "protocol_version": snapshot.protocol_version,
                    "tools_trust": "untrusted_remote_server",
                    "execution_enabled": False,
                    "tools": [{
                        "name": tool.name, "description": tool.description,
                        "input_schema": self._plain_json(tool.input_schema),
                        "metadata_trust": "untrusted_remote_server",
                        "read_only": False, "execution_enabled": False,
                    } for tool in snapshot.tools],
                })
            registry = await asyncio.to_thread(
                self.mcp_runtime.list_policies,
                owner_id=user.user_id, provider_id=provider_id, account_id=account_id,
            )
            return await self._respond(send, 200, {
                "provider_id": provider_id, "account_id": account_id,
                "revision": registry.revision,
                "policies": [{
                    "tool_name": policy.tool_name, "risk": policy.risk.value,
                    "required_scopes": list(policy.required_scopes),
                    "version": policy.version, "enabled": policy.enabled,
                } for policy in registry.policies],
            })
        except ValueError as exc:
            return await self._value_error(send, exc)
        except Exception:
            return await self._respond(send, 502, {"error": "mcp_management_unavailable"})

    async def _mutate(self, scope, receive, send, parts, method):
        provider_id, account_id, tool_name = parts
        try:
            authenticated = await self._authenticate_connection(
                scope, send, provider_id, account_id,
            )
            if authenticated is None:
                return
            user, connection = authenticated
            payload = await self._read_json_body(receive)
            if method == "PUT":
                if set(payload) != {"enabled", "expected_revision", "confirm"}:
                    return await self._respond(send, 400, {"error": "invalid_request"})
                if type(payload["enabled"]) is not bool or payload["confirm"] is not True:
                    return await self._respond(send, 400, {"error": "explicit_confirmation_required"})
                revision = await asyncio.to_thread(
                    self.mcp_runtime.set_tool_enabled,
                    owner_id=user.user_id, provider_id=provider_id,
                    account_id=account_id, tool_name=tool_name,
                    enabled=payload["enabled"],
                    expected_revision=payload["expected_revision"],
                    owner_confirmed=payload["confirm"],
                    granted_scopes=connection.scopes,
                )
            else:
                if set(payload) != {"expected_revision", "confirm"} or payload["confirm"] is not True:
                    return await self._respond(send, 400, {"error": "explicit_confirmation_required"})
                revision = await asyncio.to_thread(
                    self.mcp_runtime.remove_tool_policy,
                    owner_id=user.user_id, provider_id=provider_id,
                    account_id=account_id, tool_name=tool_name,
                    expected_revision=payload["expected_revision"],
                    owner_confirmed=payload["confirm"],
                )
            registry = await asyncio.to_thread(
                self.mcp_runtime.list_policies,
                owner_id=user.user_id, provider_id=provider_id, account_id=account_id,
            )
            return await self._respond(send, 200, {
                "provider_id": provider_id, "account_id": account_id,
                "revision": revision, "policies": [{
                    "tool_name": policy.tool_name, "risk": policy.risk.value,
                    "required_scopes": list(policy.required_scopes),
                    "version": policy.version, "enabled": policy.enabled,
                } for policy in registry.policies],
            })
        except MCPToolPolicyConflict:
            return await self._respond(send, 409, {"error": "stale_policy_revision"})
        except ValueError as exc:
            return await self._value_error(send, exc)
        except Exception:
            return await self._respond(send, 502, {"error": "mcp_management_unavailable"})

    @staticmethod
    async def _read_json_body(receive):
        chunks = []
        size = 0
        while True:
            message = await receive()
            if message.get("type") != "http.request":
                raise ValueError("invalid_request_body")
            chunk = message.get("body", b"")
            if not isinstance(chunk, bytes):
                raise ValueError("invalid_request_body")
            size += len(chunk)
            if size > _MAX_BODY:
                raise ValueError("request_body_too_large")
            chunks.append(chunk)
            if not message.get("more_body", False):
                break
        try:
            def unique_object(pairs):
                result = {}
                for key, value in pairs:
                    if key in result:
                        raise ValueError("duplicate_json_key")
                    result[key] = value
                return result
            value = json.loads(b"".join(chunks), object_pairs_hook=unique_object)
        except (json.JSONDecodeError, UnicodeDecodeError, RecursionError):
            raise ValueError("invalid_json") from None
        if not isinstance(value, dict):
            raise ValueError("json_object_required")
        return value

    @staticmethod
    async def _value_error(send, exc):
        code = str(exc)
        if code == "mcp_provider_not_configured":
            return await AuthenticatedMCPManagementAPI._respond(send, 404, {"error": "provider_not_configured"})
        if code == "required_oauth_scope_missing":
            return await AuthenticatedMCPManagementAPI._respond(send, 403, {"error": "required_oauth_scope_missing"})
        if code == "trusted_tool_definition_not_configured":
            return await AuthenticatedMCPManagementAPI._respond(send, 404, {"error": "trusted_tool_not_configured"})
        if code == "policy_not_found":
            return await AuthenticatedMCPManagementAPI._respond(send, 404, {"error": "policy_not_found"})
        if code in {"invalid_expected_revision", "invalid_tool_name", "policy_enabled_invalid"}:
            return await AuthenticatedMCPManagementAPI._respond(send, 400, {"error": "invalid_request"})
        if code in {"explicit_owner_confirmation_required"}:
            return await AuthenticatedMCPManagementAPI._respond(send, 400, {"error": "explicit_confirmation_required"})
        if code == "request_body_too_large":
            return await AuthenticatedMCPManagementAPI._respond(send, 413, {"error": "request_body_too_large"})
        return await AuthenticatedMCPManagementAPI._respond(send, 400, {"error": "invalid_request"})

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
