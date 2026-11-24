"""Fail-closed readiness probes for the production authorization boundary.

Mount this small ASGI application on the internal health listener. It does not
replace application liveness checks or the independent anchor's own readiness.
"""
from __future__ import annotations

import json

from core.production_security import ProductionSecurityContext


class ProductionSecurityReadinessASGI:
    """Expose liveness and live audit-integrity readiness for production."""

    def __init__(self, security: ProductionSecurityContext):
        if not isinstance(security, ProductionSecurityContext):
            raise TypeError("trusted_production_security_context_required")
        self.security = security

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self._respond(send, 404, {"status": "not_found"})
            return
        path = scope.get("path")
        method = scope.get("method", "GET").upper()
        if method != "GET":
            await self._respond(send, 405, {"status": "method_not_allowed"},
                                [(b"allow", b"GET")])
            return
        if path == "/health/live":
            await self._respond(send, 200, {"status": "alive"})
            return
        if path == "/health/ready":
            try:
                self.security.verify_audit_integrity()
            except Exception:
                await self._respond(send, 503, {"status": "not_ready"})
                return
            await self._respond(send, 200, {"status": "ready"})
            return
        await self._respond(send, 404, {"status": "not_found"})

    @staticmethod
    async def _respond(send, status, payload, extra_headers=()):
        body = json.dumps(payload, separators=(",", ":")).encode("ascii")
        headers = [
            (b"content-type", b"application/json; charset=utf-8"),
            (b"cache-control", b"no-store"),
            (b"pragma", b"no-cache"),
            (b"x-content-type-options", b"nosniff"),
            (b"content-length", str(len(body)).encode("ascii")),
            *extra_headers,
        ]
        await send({"type": "http.response.start", "status": status, "headers": headers})
        await send({"type": "http.response.body", "body": body})
