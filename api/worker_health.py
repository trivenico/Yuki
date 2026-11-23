"""Administrator-only read API for durable worker operational health."""
from __future__ import annotations

import asyncio
import json

from automation.worker_registry import WorkerRegistry


class AuthenticatedWorkerHealthAPI:
    """Expose sanitized worker health only to authenticated administrators."""

    def __init__(self, app, auth_service, registry: WorkerRegistry):
        if not isinstance(registry, WorkerRegistry):
            raise TypeError("registry must be a WorkerRegistry.")
        self.app = app
        self.auth = auth_service
        self.registry = registry

    def __getattr__(self, name):
        return getattr(self.app, name)

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or scope.get("path") != "/v1/admin/workers":
            await self.app(scope, receive, send)
            return
        method = scope.get("method", "GET").upper()
        if method not in {"GET", "HEAD"}:
            return await self._respond(send, 405, {"error": "method_not_allowed"}, method)
        headers = {
            key.lower(): value for key, value in scope.get("headers", [])
            if isinstance(key, bytes) and isinstance(value, bytes)
        }
        authorization = headers.get(b"authorization", b"").decode("latin-1")
        scheme, separator, token = authorization.partition(" ")
        if not separator or scheme.lower() != "bearer" or not token or len(token) > 256:
            return await self._respond(send, 401, {"error": "authentication_required"}, method)
        user = await asyncio.to_thread(self.auth.resolve_session, token)
        if user is None:
            return await self._respond(send, 401, {"error": "authentication_required"}, method)
        if user.role != "admin":
            return await self._respond(send, 403, {"error": "administrator_required"}, method)
        try:
            records = await asyncio.to_thread(
                self.registry.list_workers, stale_after_seconds=60, limit=200,
            )
            return await self._respond(send, 200, {
                "workers": [{
                    "worker_id": item.worker_id,
                    "status": item.status,
                    "started_at": item.started_at,
                    "last_seen_at": item.last_seen_at,
                    "metadata": item.metadata,
                } for item in records],
            }, method)
        except Exception:
            return await self._respond(send, 503, {"error": "worker_health_unavailable"}, method)

    @staticmethod
    async def _respond(send, status, payload, method="GET"):
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        await send({
            "type": "http.response.start", "status": status,
            "headers": [
                (b"content-type", b"application/json; charset=utf-8"),
                (b"content-length", str(len(body)).encode("ascii")),
                (b"cache-control", b"no-store"),
                (b"x-content-type-options", b"nosniff"),
            ],
        })
        await send({"type": "http.response.body", "body": b"" if method == "HEAD" else body})
