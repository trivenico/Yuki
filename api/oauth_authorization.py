"""Authenticated OAuth authorization start and callback endpoints."""
from __future__ import annotations

import asyncio
import json
import re

from core.oauth_connection_registry import SQLiteOAuthConnectionRegistry

_PROVIDER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@+-]{0,127}$")
_MAX_BODY = 10_000
_PREFIX = "/v1/integrations/oauth/providers"


class AuthenticatedOAuthAuthorizationAPI:
    """Expose only host-configured OAuth services; never accept provider URLs."""

    def __init__(self, app, auth_service, registry: SQLiteOAuthConnectionRegistry,
                 authorization_service_for_provider):
        if not isinstance(registry, SQLiteOAuthConnectionRegistry):
            raise TypeError("registry must be a SQLiteOAuthConnectionRegistry")
        if not callable(authorization_service_for_provider):
            raise TypeError("trusted authorization service resolver is required")
        self.app = app
        self.auth = auth_service
        self.registry = registry
        self._service_for_provider = authorization_service_for_provider

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
        match = re.fullmatch(
            re.escape(_PREFIX) + r"/([A-Za-z0-9][A-Za-z0-9_.:@+-]{0,127})/(authorize|callback)",
            path,
        )
        if match is None:
            return await self._respond(send, 404, {"error": "not_found"})
        if scope.get("method", "").upper() != "POST":
            return await self._respond(send, 405, {"error": "method_not_allowed"})
        try:
            token = self._session_token(scope)
            if token is None:
                return await self._respond(send, 401, {"error": "authentication_required"})
            user = await asyncio.to_thread(self.auth.resolve_session, token)
            if user is None:
                return await self._respond(send, 401, {"error": "authentication_required"})
            provider_id, action = match.groups()
            service = self._service_for_provider(provider_id)
            if service is None:
                return await self._respond(send, 404, {"error": "provider_not_configured"})
            if action == "authorize":
                payload = await self._body(receive)
                if payload:
                    return await self._respond(send, 400, {"error": "invalid_request"})
                result = await asyncio.to_thread(
                    service.begin, owner_id=user.user_id, session_id=token,
                )
                if (not isinstance(result, dict)
                        or not isinstance(result.get("authorization_url"), str)
                        or len(result["authorization_url"]) > 8192
                        or type(result.get("expires_at")) is not int):
                    return await self._respond(send, 502, {"error": "authorization_start_failed"})
                return await self._respond(send, 200, {
                    "authorization_url": result["authorization_url"],
                    "expires_at": result["expires_at"],
                })

            payload = await self._body(receive)
            if set(payload) != {"callback_url"} or not isinstance(payload["callback_url"], str):
                return await self._respond(send, 400, {"error": "invalid_request"})
            result = await asyncio.to_thread(
                service.complete, owner_id=user.user_id, session_id=token,
                callback_url=payload["callback_url"],
            )
            if (not isinstance(result, dict)
                    or result.get("provider_id") != provider_id
                    or not isinstance(result.get("account_id"), str)
                    or not isinstance(result.get("scopes"), (tuple, list))):
                return await self._respond(send, 502, {"error": "authorization_completion_failed"})
            record = await asyncio.to_thread(
                self.registry.register, owner_id=user.user_id,
                provider_id=result["provider_id"], account_id=result["account_id"],
                scopes=tuple(result["scopes"]),
            )
            return await self._respond(send, 200, {
                "connected": True, "provider_id": record.provider_id,
                "account_id": record.account_id, "scopes": list(record.scopes),
                "connected_at": record.connected_at,
            })
        except Exception:
            # Callback URLs contain authorization codes; never echo or log them.
            return await self._respond(send, 400, {"error": "authorization_rejected"})

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
    async def _body(receive):
        chunks = bytearray()
        while True:
            message = await receive()
            if message.get("type") != "http.request":
                raise ValueError("invalid request body")
            chunks.extend(message.get("body", b""))
            if len(chunks) > _MAX_BODY:
                raise ValueError("request body too large")
            if not message.get("more_body", False):
                break
        if not chunks:
            return {}
        value = json.loads(chunks.decode("utf-8"))
        if not isinstance(value, dict):
            raise ValueError("request body must be an object")
        return value

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
