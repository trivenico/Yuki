"""Authenticated owner API for listing and disconnecting OAuth integrations."""
from __future__ import annotations

import asyncio
import json
import re

from core.oauth_connection_registry import SQLiteOAuthConnectionRegistry

_PROVIDER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@+-]{0,127}$")
_ACCOUNT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@+-]{0,127}$")


class AuthenticatedOAuthConnectionAPI:
    """Expose only non-secret connection metadata and owner-initiated disconnect."""

    def __init__(self, app, auth_service, registry: SQLiteOAuthConnectionRegistry,
                 disconnect_service_for_provider):
        if not isinstance(registry, SQLiteOAuthConnectionRegistry):
            raise TypeError("registry must be a SQLiteOAuthConnectionRegistry")
        if not callable(disconnect_service_for_provider):
            raise TypeError("trusted disconnect service resolver is required")
        self.app = app
        self.auth = auth_service
        self.registry = registry
        self._disconnect_for_provider = disconnect_service_for_provider

    def __getattr__(self, name):
        return getattr(self.app, name)

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "/")
        prefix = "/v1/integrations/oauth/connections"
        if path != prefix and not path.startswith(prefix + "/"):
            await self.app(scope, receive, send)
            return
        if scope.get("method", "GET").upper() not in {"GET", "DELETE"}:
            return await self._respond(send, 405, {"error": "method_not_allowed"})
        try:
            user = await self._user(scope)
            if user is None:
                return await self._respond(send, 401, {"error": "authentication_required"})
            if path == prefix and scope.get("method", "GET").upper() == "GET":
                records = await asyncio.to_thread(
                    self.registry.list_for_owner, owner_id=user.user_id
                )
                return await self._respond(send, 200, {"connections": [
                    {"provider_id": r.provider_id, "account_id": r.account_id,
                     "scopes": list(r.scopes), "connected_at": r.connected_at,
                     "status": "connected"} for r in records
                ]})
            match = re.fullmatch(re.escape(prefix) + r"/([A-Za-z0-9][A-Za-z0-9_.:@+-]{0,127})/([A-Za-z0-9][A-Za-z0-9_.:@+-]{0,127})", path)
            if match is None or scope.get("method", "").upper() != "DELETE":
                return await self._respond(send, 404, {"error": "not_found"})
            provider_id, account_id = match.groups()
            record = await asyncio.to_thread(
                self.registry.get, owner_id=user.user_id,
                provider_id=provider_id, account_id=account_id,
            )
            if record is None:
                return await self._respond(send, 404, {"error": "connection_not_found"})
            try:
                service = self._disconnect_for_provider(provider_id)
                result = await asyncio.to_thread(
                    service.disconnect, owner_id=user.user_id, account_id=account_id
                )
            except Exception:
                return await self._respond(send, 502, {"error": "disconnect_failed"})
            if not isinstance(result, dict) or result.get("disconnected") is not True:
                return await self._respond(send, 502, {"error": "disconnect_failed"})
            await asyncio.to_thread(
                self.registry.remove, owner_id=user.user_id,
                provider_id=provider_id, account_id=account_id,
            )
            return await self._respond(send, 200, {
                "disconnected": True,
                "revocation_attempted": result.get("revocation_attempted") is True,
                "remote_revoked": result.get("remote_revoked") is True,
            })
        except Exception:
            return await self._respond(send, 400, {"error": "invalid_request"})

    async def _user(self, scope):
        headers = {
            key.lower(): value for key, value in scope.get("headers", [])
            if isinstance(key, bytes) and isinstance(value, bytes)
        }
        raw = headers.get(b"authorization", b"").decode("latin-1")
        scheme, separator, token = raw.partition(" ")
        if not separator or scheme.lower() != "bearer" or not token or len(token) > 256:
            return None
        return await asyncio.to_thread(self.auth.resolve_session, token)

    @staticmethod
    async def _respond(send, status, payload):
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        await send({"type": "http.response.start", "status": status, "headers": [
            (b"content-type", b"application/json; charset=utf-8"),
            (b"content-length", str(len(body)).encode("ascii")),
            (b"cache-control", b"no-store"),
            (b"x-content-type-options", b"nosniff"),
        ]})
        await send({"type": "http.response.body", "body": body})
