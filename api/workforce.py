"""Authenticated, read-only HTTP endpoints for Yuki's workforce dashboard."""
from __future__ import annotations

import asyncio
import json
import re
from urllib.parse import parse_qs

from core.auth import BuiltInAuthService
from core.durable_task_engine import TaskNotFound
from core.workforce_dashboard import WorkforceDashboard

_QUERY_KEYS = {"max_goals"}
_EMPLOYEE_QUERY_KEYS = {"department", "status", "search", "page", "page_size"}
_GOAL_QUERY_KEYS = {"after_event_id", "event_limit"}


class AuthenticatedWorkforceDashboardAPI:
    """Protect workforce visibility behind existing Yuki bearer sessions."""

    def __init__(self, app, auth_service: BuiltInAuthService, dashboard: WorkforceDashboard):
        if not isinstance(auth_service, BuiltInAuthService):
            raise TypeError("auth_service must be a BuiltInAuthService")
        if not isinstance(dashboard, WorkforceDashboard):
            raise TypeError("dashboard must be a WorkforceDashboard")
        self.app = app
        self.auth = auth_service
        self.dashboard = dashboard

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or not str(scope.get("path", "")).startswith("/v1/workforce"):
            await self.app(scope, receive, send)
            return
        method = scope.get("method", "GET").upper()
        if method != "GET":
            return await self._respond(send, 405, {"error": "method_not_allowed"})
        user = await self._principal(scope)
        if user is None:
            return await self._respond(send, 401, {"error": "authentication_required"})
        if user.role not in {"admin", "operator"}:
            return await self._respond(send, 403, {"error": "workforce_access_denied"})

        path = scope.get("path", "")
        try:
            query = self._query(scope)
            if path == "/v1/workforce/dashboard":
                if set(query) - _QUERY_KEYS:
                    return await self._respond(send, 400, {"error": "invalid_query"})
                max_goals = self._integer(query.get("max_goals", "100"), "max_goals")
                result = await asyncio.to_thread(self.dashboard.snapshot, max_goals=max_goals)
            elif path == "/v1/workforce/employees":
                if set(query) - _EMPLOYEE_QUERY_KEYS:
                    return await self._respond(send, 400, {"error": "invalid_query"})
                page = self._integer(query.get("page", "1"), "page")
                page_size = self._integer(query.get("page_size", "25"), "page_size")
                result = await asyncio.to_thread(
                    self.dashboard.employee_directory,
                    department=query.get("department"),
                    status=query.get("status"),
                    search=query.get("search"),
                    page=page,
                    page_size=page_size,
                )
            elif re.fullmatch(r"/v1/workforce/goals/[A-Za-z0-9][A-Za-z0-9_.:@+-]{0,255}", path):
                if set(query) - _GOAL_QUERY_KEYS:
                    return await self._respond(send, 400, {"error": "invalid_query"})
                goal_id = path.rsplit("/", 1)[-1]
                after_event_id = self._integer(query.get("after_event_id", "0"), "after_event_id", max_digits=18)
                event_limit = self._integer(query.get("event_limit", "100"), "event_limit")
                result = await asyncio.to_thread(
                    self.dashboard.goal_detail, goal_id,
                    after_event_id=after_event_id, event_limit=event_limit,
                )
            elif path == "/v1/workforce/departments":
                if set(query) != {"name"}:
                    return await self._respond(send, 400, {"error": "department_name_required"})
                result = await asyncio.to_thread(self.dashboard.department_detail, query["name"])
            else:
                return await self._respond(send, 404, {"error": "not_found"})
        except (KeyError, TaskNotFound):
            return await self._respond(send, 404, {"error": "resource_not_found"})
        except (TypeError, ValueError):
            return await self._respond(send, 400, {"error": "invalid_query"})
        except Exception:
            return await self._respond(send, 503, {"error": "workforce_dashboard_unavailable"})
        return await self._respond(send, 200, result)

    async def _principal(self, scope):
        headers = {
            key.lower(): value for key, value in scope.get("headers", [])
            if isinstance(key, bytes) and isinstance(value, bytes)
        }
        authorization = headers.get(b"authorization", b"").decode("latin-1")
        scheme, separator, token = authorization.partition(" ")
        if not separator or scheme.lower() != "bearer" or not token or len(token) > 256:
            return None
        return await asyncio.to_thread(self.auth.resolve_session, token)

    @staticmethod
    def _query(scope):
        raw = scope.get("query_string", b"").decode("ascii", "strict")
        parsed = parse_qs(raw, keep_blank_values=False, max_num_fields=8, strict_parsing=True)
        if any(len(values) != 1 for values in parsed.values()):
            raise ValueError("duplicate query parameter")
        return {key: values[0] for key, values in parsed.items()}

    @staticmethod
    def _integer(value, name, *, max_digits=4):
        if not re.fullmatch(rf"[0-9]{{1,{max_digits}}}", value):
            raise ValueError(f"{name} must be an integer")
        return int(value)

    @staticmethod
    async def _respond(send, status, payload):
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        await send({
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json; charset=utf-8"),
                (b"content-length", str(len(body)).encode("ascii")),
                (b"cache-control", b"no-store"),
                (b"x-content-type-options", b"nosniff"),
            ],
        })
        await send({"type": "http.response.body", "body": body})
