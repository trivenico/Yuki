"""Authenticated, read-only HTTP endpoints for Yuki's workforce dashboard."""
from __future__ import annotations

import asyncio
import json
import re
from urllib.parse import parse_qs

from core.auth import BuiltInAuthService
from core.approval_audit import SQLiteApprovalAuditLog
from core.pending_approvals import SQLitePendingApprovalStore
from core.approval_preview import confirmation_digest, redact_preview
from core.durable_task_engine import TaskConflict, TaskNotFound
from core.workforce_ceo import WorkforceCEO
from core.workforce_dashboard import WorkforceDashboard

_QUERY_KEYS = {"max_goals"}
_EMPLOYEE_QUERY_KEYS = {"department", "status", "search", "page", "page_size"}
_GOAL_QUERY_KEYS = {"after_event_id", "event_limit"}
_APPROVAL_QUERY_KEYS = {"limit"}
_IMPROVEMENT_QUERY_KEYS = {"limit"}
_MAX_IMPROVEMENT_BODY_BYTES = 16_384
_MAX_GOAL_BODY_BYTES = 16_384


class AuthenticatedWorkforceDashboardAPI:
    """Protect workforce visibility behind existing Yuki bearer sessions."""

    def __init__(
        self, app, auth_service: BuiltInAuthService, dashboard: WorkforceDashboard,
        *, pending_approvals: SQLitePendingApprovalStore | None = None,
        approval_audit: SQLiteApprovalAuditLog | None = None,
        workforce_ceo: WorkforceCEO | None = None,
    ):
        if not isinstance(auth_service, BuiltInAuthService):
            raise TypeError("auth_service must be a BuiltInAuthService")
        if not isinstance(dashboard, WorkforceDashboard):
            raise TypeError("dashboard must be a WorkforceDashboard")
        self.app = app
        self.auth = auth_service
        self.dashboard = dashboard
        if workforce_ceo is not None and not isinstance(workforce_ceo, WorkforceCEO):
            raise TypeError("workforce_ceo must be a WorkforceCEO")
        if workforce_ceo is not None and workforce_ceo.task_engine is not dashboard.task_engine:
            raise ValueError("workforce_ceo must use the dashboard task engine")
        self.workforce_ceo = workforce_ceo
        if pending_approvals is not None and not isinstance(pending_approvals, SQLitePendingApprovalStore):
            raise TypeError("pending_approvals must be a SQLitePendingApprovalStore")
        if approval_audit is not None and not isinstance(approval_audit, SQLiteApprovalAuditLog):
            raise TypeError("approval_audit must be a SQLiteApprovalAuditLog")
        self.pending_approvals = pending_approvals
        self.approval_audit = approval_audit

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or not str(scope.get("path", "")).startswith("/v1/workforce"):
            await self.app(scope, receive, send)
            return
        method = scope.get("method", "GET").upper()
        path = scope.get("path", "")
        if method == "POST" and path == "/v1/workforce/goals":
            return await self._submit_goal(scope, receive, send)
        if method == "POST" and re.fullmatch(r"/v1/workforce/improvement-proposals/[A-Za-z0-9][A-Za-z0-9_.:@+-]{0,255}/transition", path):
            return await self._transition_improvement_proposal(path, scope, receive, send)
        if method != "GET":
            return await self._respond(send, 405, {"error": "method_not_allowed"})
        principal = await self._principal(scope)
        if principal is None:
            return await self._respond(send, 401, {"error": "authentication_required"})
        user, session_token = principal
        if user.role not in {"admin", "operator"}:
            return await self._respond(send, 403, {"error": "workforce_access_denied"})

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
                goal = await asyncio.to_thread(self.dashboard.task_engine.get_goal, goal_id)
                if goal.get("owner_id") != user.user_id and user.role != "admin":
                    return await self._respond(send, 404, {"error": "resource_not_found"})
                after_event_id = self._integer(query.get("after_event_id", "0"), "after_event_id", max_digits=18)
                event_limit = self._integer(query.get("event_limit", "100"), "event_limit")
                result = await asyncio.to_thread(
                    self.dashboard.goal_detail, goal_id,
                    after_event_id=after_event_id, event_limit=event_limit,
                )
            elif path == "/v1/workforce/improvement-proposals":
                if set(query) - _IMPROVEMENT_QUERY_KEYS:
                    return await self._respond(send, 400, {"error": "invalid_query"})
                limit = self._integer(query.get("limit", "100"), "limit")
                if not 1 <= limit <= 500:
                    return await self._respond(send, 400, {"error": "invalid_query"})
                proposals = await asyncio.to_thread(self.dashboard.task_engine.list_improvement_proposals, limit=limit)
                result = {"proposals": proposals, "count": len(proposals)}
            elif re.fullmatch(r"/v1/workforce/improvement-proposals/[A-Za-z0-9][A-Za-z0-9_.:@+-]{0,255}", path):
                if query:
                    return await self._respond(send, 400, {"error": "invalid_query"})
                proposal_id = path.rsplit("/", 1)[-1]
                result = await asyncio.to_thread(self.dashboard.task_engine.get_improvement_proposal, proposal_id)
            elif path == "/v1/workforce/approvals":
                if set(query) - _APPROVAL_QUERY_KEYS:
                    return await self._respond(send, 400, {"error": "invalid_query"})
                if self.pending_approvals is None:
                    return await self._respond(send, 503, {"error": "approval_inbox_unavailable"})
                limit = self._integer(query.get("limit", "100"), "limit")
                pending = await asyncio.to_thread(self.pending_approvals.list_pending, actor=user.user_id, session_id=session_token, limit=limit)
                result = {"pending": pending, "count": len(pending)}
            elif re.fullmatch(r"/v1/workforce/approvals/[A-Za-z0-9_-]{16,128}", path):
                if query:
                    return await self._respond(send, 400, {"error": "invalid_query"})
                if self.pending_approvals is None:
                    return await self._respond(send, 503, {"error": "approval_inbox_unavailable"})
                pending_id = path.rsplit("/", 1)[-1]
                item = await asyncio.to_thread(
                    self.pending_approvals.get_for_actor_session,
                    pending_id, actor=user.user_id, session_id=session_token,
                )
                if item is None:
                    return await self._respond(send, 404, {"error": "approval_not_found"})
                result = {
                    "pending_id": item.pending_id,
                    "skill_id": item.skill_id,
                    "expires_at": item.expires_at,
                    "action": redact_preview(item.action),
                    "confirmation_digest": confirmation_digest(item.action),
                    "risk": "explicit_confirmation_required",
                    "execution": "not_executed",
                }
            elif path == "/v1/workforce/approvals/audit":
                if set(query) - _APPROVAL_QUERY_KEYS:
                    return await self._respond(send, 400, {"error": "invalid_query"})
                if self.approval_audit is None:
                    return await self._respond(send, 503, {"error": "approval_audit_unavailable"})
                limit = self._integer(query.get("limit", "100"), "limit")
                events, valid = await asyncio.gather(
                    asyncio.to_thread(self.approval_audit.list_events, limit=limit),
                    asyncio.to_thread(self.approval_audit.verify_chain),
                )
                result = {"events": events, "count": len(events), "chain_valid": valid}
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

    async def _transition_improvement_proposal(self, path, scope, receive, send):
        principal = await self._principal(scope)
        if principal is None:
            return await self._respond(send, 401, {"error": "authentication_required"})
        user, _session_token = principal
        if user.role not in {"admin", "operator"}:
            return await self._respond(send, 403, {"error": "workforce_access_denied"})
        headers = {key.lower(): value for key, value in scope.get("headers", []) if isinstance(key, bytes) and isinstance(value, bytes)}
        content_type = headers.get(b"content-type", b"").split(b";", 1)[0].strip().lower()
        if content_type != b"application/json":
            return await self._respond(send, 415, {"error": "application_json_required"})
        body = bytearray()
        more_body = True
        while more_body:
            message = await receive()
            if message.get("type") != "http.request":
                continue
            chunk = message.get("body", b"")
            if not isinstance(chunk, bytes):
                return await self._respond(send, 400, {"error": "invalid_request_body"})
            body.extend(chunk)
            if len(body) > _MAX_IMPROVEMENT_BODY_BYTES:
                return await self._respond(send, 413, {"error": "request_body_too_large"})
            more_body = bool(message.get("more_body", False))
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return await self._respond(send, 400, {"error": "invalid_json"})
        allowed = {"expected_state", "new_state", "decision_note", "outcome"}
        if not isinstance(payload, dict) or not {"expected_state", "new_state"}.issubset(payload) or set(payload) - allowed:
            return await self._respond(send, 400, {"error": "invalid_transition_request"})
        expected_state, new_state = payload["expected_state"], payload["new_state"]
        decision_note, outcome = payload.get("decision_note"), payload.get("outcome")
        if not isinstance(expected_state, str) or not isinstance(new_state, str) or (decision_note is not None and not isinstance(decision_note, str)) or (outcome is not None and not isinstance(outcome, dict)):
            return await self._respond(send, 400, {"error": "invalid_transition_request"})
        proposal_id = path.split("/")[-2]
        try:
            result = await asyncio.to_thread(self.dashboard.task_engine.transition_improvement_proposal, proposal_id, expected_state=expected_state, new_state=new_state, actor_id=user.user_id, decision_note=decision_note, outcome=outcome)
        except TaskConflict:
            return await self._respond(send, 409, {"error": "proposal_state_conflict"})
        except TaskNotFound:
            return await self._respond(send, 404, {"error": "proposal_not_found"})
        except (TypeError, ValueError):
            return await self._respond(send, 400, {"error": "invalid_transition_request"})
        except Exception:
            return await self._respond(send, 503, {"error": "improvement_proposal_unavailable"})
        return await self._respond(send, 200, result)

    async def _submit_goal(self, scope, receive, send):
        principal = await self._principal(scope)
        if principal is None:
            return await self._respond(send, 401, {"error": "authentication_required"})
        user, _session_token = principal
        if user.role not in {"admin", "operator"}:
            return await self._respond(send, 403, {"error": "workforce_access_denied"})
        if self.workforce_ceo is None:
            return await self._respond(send, 503, {"error": "goal_submission_unavailable"})
        headers = {
            key.lower(): value for key, value in scope.get("headers", [])
            if isinstance(key, bytes) and isinstance(value, bytes)
        }
        content_type = headers.get(b"content-type", b"").split(b";", 1)[0].strip().lower()
        if content_type != b"application/json":
            return await self._respond(send, 415, {"error": "application_json_required"})
        idempotency_key = headers.get(b"idempotency-key", b"").decode("latin-1")
        if not 1 <= len(idempotency_key) <= 256 or any(ord(ch) < 33 or ord(ch) > 126 for ch in idempotency_key):
            return await self._respond(send, 400, {"error": "valid_idempotency_key_required"})
        body = bytearray()
        more_body = True
        while more_body:
            message = await receive()
            if message.get("type") != "http.request":
                continue
            chunk = message.get("body", b"")
            if not isinstance(chunk, bytes):
                return await self._respond(send, 400, {"error": "invalid_request_body"})
            body.extend(chunk)
            if len(body) > _MAX_GOAL_BODY_BYTES:
                return await self._respond(send, 413, {"error": "request_body_too_large"})
            more_body = bool(message.get("more_body", False))
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return await self._respond(send, 400, {"error": "invalid_json"})
        if not isinstance(payload, dict) or set(payload) != {"goal"}:
            return await self._respond(send, 400, {"error": "invalid_goal_request"})
        goal = payload["goal"]
        if not isinstance(goal, str) or not goal.strip() or len(goal) > 4000:
            return await self._respond(send, 400, {"error": "goal_must_be_1_to_4000_characters"})
        request = {"goal": goal.strip()}
        try:
            intake = await asyncio.to_thread(
                self.dashboard.task_engine.create_owned_goal,
                user.user_id, idempotency_key, request,
            )
            execution = await asyncio.to_thread(
                self.workforce_ceo.plan_and_dispatch,
                request["goal"], goal_id=intake["goal"]["id"],
            )
            report = await asyncio.to_thread(self.workforce_ceo.report, execution)
        except TaskConflict:
            return await self._respond(send, 409, {"error": "idempotency_conflict"})
        except Exception:
            # The durable goal and request key remain available for an exact retry.
            return await self._respond(send, 422, {
                "error": "goal_planning_or_dispatch_failed",
                "retryable": True,
            })
        return await self._respond(send, 202, {
            "goal_id": intake["goal"]["id"],
            "created": intake["created"],
            "replayed": not intake["created"],
            "execution": "queued",
            "report": report,
        })

    async def _principal(self, scope):
        headers = {
            key.lower(): value for key, value in scope.get("headers", [])
            if isinstance(key, bytes) and isinstance(value, bytes)
        }
        authorization = headers.get(b"authorization", b"").decode("latin-1")
        scheme, separator, token = authorization.partition(" ")
        if not separator or scheme.lower() != "bearer" or not token or len(token) > 256:
            return None
        user = await asyncio.to_thread(self.auth.resolve_session, token)
        return (user, token) if user is not None else None

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
