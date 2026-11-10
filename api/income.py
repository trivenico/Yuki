"""Authenticated API for the owner's income opportunity pipeline."""
from __future__ import annotations

import asyncio
from dataclasses import asdict
import json
import re
from urllib.parse import parse_qs

from core.income_opportunities import SQLiteIncomeOpportunityStore


_MAX_BODY = 12_000
_MAX_QUERY = 2_048
_ID = re.compile(r"^[0-9a-f]{32}$")
_ALLOWED_CREATE = frozenset({
    "platform_id", "title", "category", "source_url", "description",
    "evidence_level", "currency", "estimated_net_minor", "startup_cost_minor",
    "estimated_hours", "fit_score", "demand_score", "competition_score",
})


class AuthenticatedIncomeOpportunityAPI:
    """Owner-session routes; owner identity is never accepted from request data."""

    def __init__(self, app, auth_service, opportunities: SQLiteIncomeOpportunityStore):
        if not isinstance(opportunities, SQLiteIncomeOpportunityStore):
            raise TypeError("opportunities must be a SQLiteIncomeOpportunityStore")
        self.app = app
        self.auth = auth_service
        self.opportunities = opportunities

    def __getattr__(self, name):
        return getattr(self.app, name)

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        method = scope.get("method", "GET").upper()
        path = scope.get("path", "/")
        prefix = "/v1/income/opportunities"
        if path != prefix and not path.startswith(prefix + "/"):
            await self.app(scope, receive, send)
            return
        try:
            user = await self._user(scope)
            if user is None:
                return await self._respond(send, 401, {"error": "authentication_required"})
            if path == prefix:
                if method == "POST":
                    payload = await self._body(receive)
                    if not {"platform_id", "title", "category", "source_url"} <= set(payload):
                        return await self._respond(send, 400, {"error": "invalid_request"})
                    if set(payload) - _ALLOWED_CREATE:
                        return await self._respond(send, 400, {"error": "invalid_request"})
                    item = await asyncio.to_thread(
                        self.opportunities.create, user.user_id, **payload
                    )
                    return await self._respond(send, 201, {"opportunity": asdict(item)})
                if method == "GET":
                    raw_query = scope.get("query_string", b"")
                    if not isinstance(raw_query, bytes) or len(raw_query) > _MAX_QUERY:
                        return await self._respond(send, 400, {"error": "invalid_query"})
                    query = parse_qs(
                        raw_query.decode("ascii"),
                        keep_blank_values=True, strict_parsing=True,
                    )
                    if set(query) - {"status", "platform_id", "limit"} or any(
                        len(values) != 1 for values in query.values()
                    ):
                        return await self._respond(send, 400, {"error": "invalid_query"})
                    kwargs = {}
                    if "status" in query:
                        kwargs["status"] = query["status"][0]
                    if "platform_id" in query:
                        kwargs["platform_id"] = query["platform_id"][0]
                    if "limit" in query:
                        raw_limit = query["limit"][0]
                        if not re.fullmatch(r"[0-9]{1,3}", raw_limit):
                            return await self._respond(send, 400, {"error": "invalid_query"})
                        kwargs["limit"] = int(raw_limit)
                    items = await asyncio.to_thread(
                        self.opportunities.list, user.user_id, **kwargs
                    )
                    return await self._respond(send, 200, {
                        "opportunities": [asdict(item) for item in items],
                    })
                return await self._respond(send, 405, {"error": "method_not_allowed"})

            match = re.fullmatch(re.escape(prefix) + r"/([0-9a-f]{32})(/status)?", path)
            if match is None:
                return await self._respond(send, 404, {"error": "not_found"})
            opportunity_id, status_suffix = match.groups()
            if status_suffix == "/status" and method == "POST":
                payload = await self._body(receive)
                if set(payload) != {"new_status"} or not isinstance(payload["new_status"], str):
                    return await self._respond(send, 400, {"error": "invalid_request"})
                item = await asyncio.to_thread(
                    self.opportunities.transition, user.user_id, opportunity_id,
                    new_status=payload["new_status"],
                )
                return await self._respond(
                    send, 200 if item else 404,
                    {"opportunity": asdict(item)} if item else {"error": "not_found"},
                )
            if status_suffix is None and method == "GET":
                item = await asyncio.to_thread(
                    self.opportunities.get, user.user_id, opportunity_id
                )
                return await self._respond(
                    send, 200 if item else 404,
                    {"opportunity": asdict(item)} if item else {"error": "not_found"},
                )
            if status_suffix is None and method == "DELETE":
                deleted = await asyncio.to_thread(
                    self.opportunities.delete, user.user_id, opportunity_id
                )
                return await self._respond(
                    send, 200 if deleted else 404,
                    {"deleted": True} if deleted else {"error": "not_found"},
                )
            return await self._respond(send, 405, {"error": "method_not_allowed"})
        except (ValueError, TypeError, UnicodeDecodeError):
            return await self._respond(send, 400, {"error": "invalid_request"})
        except Exception:
            return await self._respond(send, 500, {"error": "internal_error"})

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
