"""Separate ASGI control plane for audit-anchor publisher administration.

Authentication is supplied by a trusted host administrator identity adapter.
The publisher API and its credentials cannot invoke these routes.
"""
from __future__ import annotations

import json

from core.audit_anchor_service import (
    AuditAnchorUnauthorized,
    SQLiteAuditAnchorService,
)


class AuditAnchorAdminASGI:
    """Admin-only publisher enrollment, revocation, and audit read API."""

    def __init__(self, service: SQLiteAuditAnchorService, authenticate_admin_scope):
        if not isinstance(service, SQLiteAuditAnchorService):
            raise TypeError("sqlite_audit_anchor_service_required")
        if not callable(authenticate_admin_scope):
            raise TypeError("trusted_admin_authenticator_required")
        self.service = service
        self.authenticate_admin_scope = authenticate_admin_scope

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or not isinstance(scope.get("path"), str):
            await self._respond(send, 404, {"error": "not_found"})
            return
        try:
            administrator = self.authenticate_admin_scope(scope)
            if not isinstance(administrator, str):
                await self._respond(send, 401, {"error": "unauthorized"})
                return
            path = scope["path"]
            method = scope.get("method", "GET").upper()
            if path == "/v1/admin/audit" and method == "GET":
                rows = self.service.administrative_audit(administrator)
                await self._respond(send, 200, {"events": list(rows)})
                return
            prefix = "/v1/admin/publishers/"
            if not path.startswith(prefix):
                await self._respond(send, 404, {"error": "not_found"})
                return
            publisher = path[len(prefix):]
            if not publisher or "/" in publisher or len(publisher) > 256:
                await self._respond(send, 400, {"error": "invalid_publisher"})
                return
            if method == "PUT":
                self.service.set_publisher(administrator, publisher, enabled=True)
            elif method == "DELETE":
                self.service.set_publisher(administrator, publisher, enabled=False)
            else:
                await self._respond(send, 405, {"error": "method_not_allowed"},
                                    [(b"allow", b"PUT, DELETE")])
                return
            await self._respond(send, 204, {})
        except AuditAnchorUnauthorized:
            await self._respond(send, 403, {"error": "forbidden"})
        except (ValueError, UnicodeError):
            await self._respond(send, 400, {"error": "invalid_request"})
        except Exception:
            await self._respond(send, 503, {"error": "service_unavailable"})

    @staticmethod
    async def _respond(send, status, payload, extra_headers=()):
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        headers = [
            (b"content-type", b"application/json; charset=utf-8"),
            (b"cache-control", b"no-store"),
            (b"x-content-type-options", b"nosniff"),
            (b"content-length", str(len(body)).encode("ascii")),
            *extra_headers,
        ]
        await send({"type": "http.response.start", "status": status, "headers": headers})
        await send({"type": "http.response.body", "body": body})
