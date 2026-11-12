"""Authenticated HTTP routes for the owner's durable capability approval inbox."""
from __future__ import annotations

import asyncio
import json
import re

from core.auth import AuthenticationError
from core.authenticated_capability_approval_service import AuthenticatedCapabilityApprovalService
from core.tool_policy import PolicyRequest, Risk

_MAX_BODY = 12_000
_REQUEST_ID = re.compile(r"^[0-9a-f]{32}$")
_REQUEST_FIELDS = frozenset({
    "principal_id", "trigger", "capability", "target", "argument_sha256",
    "risk", "run_id", "step_id", "claim_token_sha256",
})


class AuthenticatedCapabilityApprovalAPI:
    """Expose redacted approval metadata; never return one-shot credentials."""

    def __init__(self, app, service: AuthenticatedCapabilityApprovalService):
        if not isinstance(service, AuthenticatedCapabilityApprovalService):
            raise TypeError("service must be an AuthenticatedCapabilityApprovalService")
        self.app = app
        self.service = service

    def __getattr__(self, name):
        return getattr(self.app, name)

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "/")
        prefix = "/v1/approvals"
        if path != prefix and not path.startswith(prefix + "/"):
            await self.app(scope, receive, send)
            return
        method = scope.get("method", "GET").upper()
        token = self._bearer(scope)
        if token is None:
            return await self._respond(send, 401, {"error": "authentication_required"})
        try:
            if path == prefix + "/requests" and method == "POST":
                payload = await self._body(receive)
                if set(payload) - _REQUEST_FIELDS or not {
                    "principal_id", "trigger", "capability", "target",
                    "argument_sha256", "risk", "run_id", "step_id",
                } <= set(payload):
                    return await self._respond(send, 400, {"error": "invalid_request"})
                try:
                    payload["risk"] = Risk(payload["risk"])
                    request = PolicyRequest(**payload)
                except (ValueError, TypeError, KeyError):
                    return await self._respond(send, 400, {"error": "invalid_request"})
                request_id = await asyncio.to_thread(
                    self.service.request_approval, token, request
                )
                return await self._respond(send, 201, {"request_id": request_id})

            if path == prefix + "/inbox" and method == "GET":
                items = await asyncio.to_thread(self.service.pending_requests, token)
                return await self._respond(send, 200, {"requests": [dict(item) for item in items]})

            match = re.fullmatch(re.escape(prefix) + r"/requests/([0-9a-f]{32})/(approve|reject)", path)
            if match and method == "POST":
                request_id, decision = match.groups()
                payload = await self._body(receive)
                if payload:
                    return await self._respond(send, 400, {"error": "invalid_request"})
                if decision == "approve":
                    # Token delivery is exclusively through the configured trusted
                    # callback. It is never returned to this HTTP caller.
                    await asyncio.to_thread(self.service.approve_pending, token, request_id)
                    return await self._respond(send, 200, {"approved": True})
                rejected = await asyncio.to_thread(
                    self.service.reject_pending, token, request_id
                )
                return await self._respond(
                    send, 200 if rejected else 409,
                    {"rejected": True} if rejected else {"error": "request_unavailable"},
                )

            if path == prefix + "/audit" and method == "GET":
                events = await asyncio.to_thread(self.service.audit_events, token)
                return await self._respond(send, 200, {"events": [dict(item) for item in events]})
            return await self._respond(send, 405 if path in {
                prefix + "/requests", prefix + "/inbox", prefix + "/audit"
            } else 404, {"error": "method_not_allowed" if path in {
                prefix + "/requests", prefix + "/inbox", prefix + "/audit"
            } else "not_found"})
        except AuthenticationError:
            return await self._respond(send, 401, {"error": "authentication_required"})
        except PermissionError:
            return await self._respond(send, 403, {"error": "permission_denied"})
        except ValueError:
            return await self._respond(send, 400, {"error": "invalid_request"})
        except RuntimeError:
            return await self._respond(send, 503, {"error": "approval_delivery_unavailable"})
        except Exception:
            return await self._respond(send, 500, {"error": "internal_error"})

    @staticmethod
    def _bearer(scope):
        headers = {
            key.lower(): value for key, value in scope.get("headers", [])
            if isinstance(key, bytes) and isinstance(value, bytes)
        }
        raw = headers.get(b"authorization", b"").decode("latin-1")
        scheme, separator, token = raw.partition(" ")
        if not separator or scheme.lower() != "bearer" or not token or len(token) > 256:
            return None
        return token

    @staticmethod
    async def _body(receive):
        chunks = bytearray()
        while True:
            event = await receive()
            if event.get("type") == "http.disconnect":
                raise ValueError("client_disconnected")
            if event.get("type") != "http.request":
                continue
            chunks.extend(event.get("body", b""))
            if len(chunks) > _MAX_BODY:
                raise ValueError("request_too_large")
            if not event.get("more_body", False):
                break
        value = json.loads(chunks.decode("utf-8") or "{}")
        if not isinstance(value, dict):
            raise ValueError("request_must_be_object")
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
