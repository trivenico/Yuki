"""Authenticated workforce dashboard, goal-intake, and proposal endpoints."""
from __future__ import annotations

import asyncio
import hashlib
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
from core.workforce_improvement_evaluator import evaluate_improvement_snapshots

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
        if method == "POST" and path == "/v1/workforce/improvement-proposals":
            return await self._create_improvement_proposal(scope, receive, send)
        if method == "POST" and path == "/v1/workforce/experiments":
            return await self._create_workforce_experiment(scope, receive, send)
        if method == "POST" and re.fullmatch(r"/v1/workforce/experiments/[A-Za-z0-9][A-Za-z0-9_.:@+-]{0,255}/transition", path):
            return await self._transition_workforce_experiment(path, scope, receive, send)
        if method == "POST" and re.fullmatch(r"/v1/workforce/experiments/[A-Za-z0-9][A-Za-z0-9_.:@+-]{0,255}/deliveries", path):
            return await self._record_workforce_experiment_delivery(path, scope, receive, send)
        if method == "GET" and re.fullmatch(r"/v1/workforce/experiments/[A-Za-z0-9][A-Za-z0-9_.:@+-]{0,255}/treatment-exposures", path):
            principal = await self._principal(scope)
            if principal is None:
                return await self._respond(send, 401, {"error": "authentication_required"})
            user, _ = principal
            if user.role not in {"admin", "operator"}:
                return await self._respond(send, 403, {"error": "workforce_access_denied"})
            try:
                query = self._query(scope)
                if set(query) - {"limit"}:
                    return await self._respond(send, 400, {"error": "invalid_query"})
                limit = self._integer(query.get("limit", "500"), "limit")
                experiment_id = path.split("/")[-2]
                result = await asyncio.to_thread(
                    self.dashboard.task_engine.list_workforce_experiment_treatment_exposures,
                    experiment_id, limit=limit,
                )
            except TaskNotFound:
                return await self._respond(send, 404, {"error": "experiment_not_found"})
            except (TypeError, ValueError):
                return await self._respond(send, 400, {"error": "invalid_query"})
            except Exception:
                return await self._respond(send, 503, {"error": "experiment_treatment_exposures_unavailable"})
            return await self._respond(send, 200, {"treatment_exposures": result})
        if method == "GET" and re.fullmatch(r"/v1/workforce/experiments/[A-Za-z0-9][A-Za-z0-9_.:@+-]{0,255}/execution-events", path):
            principal = await self._principal(scope)
            if principal is None:
                return await self._respond(send, 401, {"error": "authentication_required"})
            user, _ = principal
            if user.role not in {"admin", "operator"}:
                return await self._respond(send, 403, {"error": "workforce_access_denied"})
            try:
                query = self._query(scope)
                if set(query) - {"limit"}:
                    return await self._respond(send, 400, {"error": "invalid_query"})
                limit = self._integer(query.get("limit", "500"), "limit")
                experiment_id = path.split("/")[-2]
                result = await asyncio.to_thread(
                    self.dashboard.task_engine.list_workforce_experiment_execution_events,
                    experiment_id, limit=limit,
                )
            except TaskNotFound:
                return await self._respond(send, 404, {"error": "experiment_not_found"})
            except (TypeError, ValueError):
                return await self._respond(send, 400, {"error": "invalid_query"})
            except Exception:
                return await self._respond(send, 503, {"error": "experiment_execution_events_unavailable"})
            return await self._respond(send, 200, {"execution_events": result})
        if method == "GET" and re.fullmatch(r"/v1/workforce/experiments/[A-Za-z0-9][A-Za-z0-9_.:@+-]{0,255}/integrity", path):
            principal = await self._principal(scope)
            if principal is None:
                return await self._respond(send, 401, {"error": "authentication_required"})
            user, _ = principal
            if user.role not in {"admin", "operator"}:
                return await self._respond(send, 403, {"error": "workforce_access_denied"})
            try:
                if self._query(scope):
                    return await self._respond(send, 400, {"error": "invalid_query"})
                experiment_id = path.split("/")[-2]
                result = await asyncio.to_thread(
                    self.dashboard.task_engine.get_workforce_experiment_integrity,
                    experiment_id,
                )
            except TaskNotFound:
                return await self._respond(send, 404, {"error": "experiment_not_found"})
            except (TypeError, ValueError):
                return await self._respond(send, 400, {"error": "invalid_query"})
            except Exception:
                return await self._respond(send, 503, {"error": "experiment_integrity_unavailable"})
            return await self._respond(send, 200, result)
        if method == "GET" and re.fullmatch(r"/v1/workforce/experiments/[A-Za-z0-9][A-Za-z0-9_.:@+-]{0,255}/deliveries", path):
            principal = await self._principal(scope)
            if principal is None:
                return await self._respond(send, 401, {"error": "authentication_required"})
            user, _ = principal
            if user.role not in {"admin", "operator"}:
                return await self._respond(send, 403, {"error": "workforce_access_denied"})
            try:
                query = self._query(scope)
                if set(query) - {"limit"}:
                    return await self._respond(send, 400, {"error": "invalid_query"})
                limit = self._integer(query.get("limit", "500"), "limit")
                experiment_id = path.split("/")[-2]
                result = await asyncio.to_thread(
                    self.dashboard.task_engine.list_workforce_experiment_deliveries,
                    experiment_id, limit=limit,
                )
            except TaskNotFound:
                return await self._respond(send, 404, {"error": "experiment_not_found"})
            except (TypeError, ValueError):
                return await self._respond(send, 400, {"error": "invalid_query"})
            except Exception:
                return await self._respond(send, 503, {"error": "experiment_delivery_unavailable"})
            return await self._respond(send, 200, {"deliveries": result})
        if method == "POST" and re.fullmatch(r"/v1/workforce/experiments/[A-Za-z0-9][A-Za-z0-9_.:@+-]{0,255}/assignments", path):
            return await self._assign_workforce_experiment_task(path, scope, receive, send)
        if method == "POST" and re.fullmatch(r"/v1/workforce/knowledge/[A-Za-z0-9][A-Za-z0-9_.:@+-]{0,255}/revisions", path):
            return await self._create_knowledge_revision(path, scope, receive, send)
        if method == "POST" and re.fullmatch(r"/v1/workforce/knowledge-revisions/[A-Za-z0-9][A-Za-z0-9_.:@+-]{0,255}/transition", path):
            return await self._transition_knowledge_revision(path, scope, receive, send)
        if method == "POST" and re.fullmatch(r"/v1/workforce/knowledge/[A-Za-z0-9][A-Za-z0-9_.:@+-]{0,255}/transition", path):
            return await self._transition_knowledge(path, scope, receive, send)
        if method == "POST" and re.fullmatch(r"/v1/workforce/improvement-proposals/[A-Za-z0-9][A-Za-z0-9_.:@+-]{0,255}/transition", path):
            return await self._transition_improvement_proposal(path, scope, receive, send)
        if method == "GET" and re.fullmatch(r"/v1/workforce/improvement-proposals/[A-Za-z0-9][A-Za-z0-9_.:@+-]{0,255}/comparison", path):
            return await self._compare_improvement_proposal(path, scope, send)
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
            if re.fullmatch(r"/v1/workforce/tasks/[A-Za-z0-9][A-Za-z0-9_.:@+-]{0,255}/knowledge-delivery-integrity", path):
                if query:
                    return await self._respond(send, 400, {"error": "invalid_query"})
                task_id = path.split("/")[-2]
                result = await asyncio.to_thread(
                    self.dashboard.task_engine.get_workforce_knowledge_delivery_integrity,
                    task_id,
                )
            elif path == "/v1/workforce/dashboard":
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
                after_event_id = self._integer(query.get("after_event_id", "0"), "after_event_id", max_digits=18)
                event_limit = self._integer(query.get("event_limit", "100"), "event_limit")
                if not 1 <= event_limit <= 500:
                    return await self._respond(send, 400, {"error": "invalid_query"})
                goal_id = path.rsplit("/", 1)[-1]
                goal = await asyncio.to_thread(self.dashboard.task_engine.get_goal, goal_id)
                if goal.get("owner_id") != user.user_id and user.role != "admin":
                    return await self._respond(send, 404, {"error": "resource_not_found"})
                result = await asyncio.to_thread(
                    self.dashboard.goal_detail, goal_id,
                    after_event_id=after_event_id, event_limit=event_limit,
                )
            elif path == "/v1/workforce/knowledge":
                if set(query) - {"limit", "domain"}:
                    return await self._respond(send, 400, {"error": "invalid_query"})
                limit = self._integer(query.get("limit", "100"), "limit")
                if not 1 <= limit <= 500:
                    return await self._respond(send, 400, {"error": "invalid_query"})
                records = await asyncio.to_thread(
                    self.dashboard.task_engine.list_verified_knowledge,
                    limit=limit, domain=query.get("domain"),
                )
                result = {"knowledge": records, "count": len(records), "visibility": "verified_only"}
            elif re.fullmatch(r"/v1/workforce/knowledge/[A-Za-z0-9][A-Za-z0-9_.:@+-]{0,255}/revisions", path):
                if query:
                    return await self._respond(send, 400, {"error": "invalid_query"})
                knowledge_id = path.split("/")[-2]
                revisions = await asyncio.to_thread(
                    self.dashboard.task_engine.list_knowledge_revisions, knowledge_id,
                )
                result = {"revisions": revisions, "count": len(revisions)}
            elif re.fullmatch(r"/v1/workforce/knowledge/[A-Za-z0-9][A-Za-z0-9_.:@+-]{0,255}", path):
                if query:
                    return await self._respond(send, 400, {"error": "invalid_query"})
                knowledge_id = path.rsplit("/", 1)[-1]
                result = await asyncio.to_thread(self.dashboard.task_engine.get_knowledge, knowledge_id)
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
            elif path == "/v1/workforce/experiments":
                if set(query) - {"limit"}:
                    return await self._respond(send, 400, {"error": "invalid_query"})
                limit = self._integer(query.get("limit", "100"), "limit")
                if not 1 <= limit <= 500:
                    return await self._respond(send, 400, {"error": "invalid_query"})
                experiments = await asyncio.to_thread(self.dashboard.task_engine.list_workforce_experiments, limit=limit)
                result = {"experiments": experiments, "count": len(experiments)}
            elif re.fullmatch(r"/v1/workforce/experiments/[A-Za-z0-9][A-Za-z0-9_.:@+-]{0,255}/assignments", path):
                if set(query) - {"limit"}:
                    return await self._respond(send, 400, {"error": "invalid_query"})
                experiment_id = path.split("/")[-2]
                limit = self._integer(query.get("limit", "500"), "limit")
                result = {"assignments": await asyncio.to_thread(self.dashboard.task_engine.list_workforce_experiment_assignments, experiment_id, limit=limit)}
            elif re.fullmatch(r"/v1/workforce/experiments/[A-Za-z0-9][A-Za-z0-9_.:@+-]{0,255}/outcomes", path):
                if query:
                    return await self._respond(send, 400, {"error": "invalid_query"})
                experiment_id = path.split("/")[-2]
                result = await asyncio.to_thread(
                    self.dashboard.task_engine.get_workforce_experiment_outcomes,
                    experiment_id,
                )
            elif re.fullmatch(r"/v1/workforce/experiments/[A-Za-z0-9][A-Za-z0-9_.:@+-]{0,255}", path):
                if query:
                    return await self._respond(send, 400, {"error": "invalid_query"})
                experiment_id = path.rsplit("/", 1)[-1]
                result = await asyncio.to_thread(self.dashboard.task_engine.get_workforce_experiment, experiment_id)
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

    async def _create_improvement_proposal(self, scope, receive, send):
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
        if not isinstance(payload, dict) or set(payload) != {"recommendation_id"}:
            return await self._respond(send, 400, {"error": "invalid_proposal_request"})
        recommendation_id = payload["recommendation_id"]
        if not isinstance(recommendation_id, str) or not 1 <= len(recommendation_id) <= 256:
            return await self._respond(send, 400, {"error": "invalid_proposal_request"})
        proposal_id = "recommendation-" + hashlib.sha256(recommendation_id.encode("utf-8")).hexdigest()
        try:
            existing = await asyncio.to_thread(
                self.dashboard.task_engine.get_improvement_proposal, proposal_id,
            )
        except TaskNotFound:
            existing = None
        except Exception:
            return await self._respond(send, 503, {"error": "improvement_proposal_unavailable"})
        if existing is not None:
            return await self._respond(send, 200, {
                "created": False, "proposal": existing["proposal"],
                "proposal_id": existing["proposal_id"], "state": existing["state"],
            })
        try:
            snapshot = await asyncio.to_thread(self.dashboard.snapshot)
            recommendations = snapshot["decision_intelligence"]["improvement_recommendations"]
            recommendation = next((item for item in recommendations if item["id"] == recommendation_id), None)
            if recommendation is None:
                return await self._respond(send, 404, {"error": "recommendation_not_found"})
            if recommendation.get("requires_human_approval") is not True or recommendation.get("automatic_action_taken") is not False:
                return await self._respond(send, 409, {"error": "recommendation_not_eligible"})
            proposal = {
                "source": "workforce_decision_intelligence",
                "recommendation_id": recommendation["id"],
                "category": recommendation["category"],
                "priority": recommendation["priority"],
                "evidence": recommendation["evidence"],
                "recommendation": recommendation["recommendation"],
                "baseline": {
                    "captured_at": self.dashboard.task_engine.clock(),
                    "scope": "bounded_workforce_dashboard_snapshot",
                    "goal_window": {
                        "max_goals": 100,
                        "included_goal_count": len(snapshot.get("goals", [])),
                    },
                    "workforce": snapshot.get("workforce", {}),
                    "tasks": snapshot.get("tasks", {}),
                    "performance": snapshot.get("performance", {}),
                },
                "requires_human_approval": True,
                "automatic_action_taken": False,
            }
            result = await asyncio.to_thread(self.dashboard.task_engine.create_improvement_proposal, proposal_id, proposal)
        except TaskConflict:
            return await self._respond(send, 409, {"error": "proposal_identity_conflict"})
        except Exception:
            return await self._respond(send, 503, {"error": "improvement_proposal_unavailable"})
        return await self._respond(send, 201 if result["created"] else 200, {
            "created": result["created"], "proposal": result["proposal"],
            "proposal_id": result["proposal_id"], "state": result["state"],
        })

    async def _compare_improvement_proposal(self, path, scope, send):
        principal = await self._principal(scope)
        if principal is None:
            return await self._respond(send, 401, {"error": "authentication_required"})
        user, _session_token = principal
        if user.role not in {"admin", "operator"}:
            return await self._respond(send, 403, {"error": "workforce_access_denied"})
        proposal_id = path.rsplit("/", 2)[-2]
        try:
            proposal = await asyncio.to_thread(self.dashboard.task_engine.get_improvement_proposal, proposal_id)
            baseline = proposal["proposal"].get("baseline")
            if not isinstance(baseline, dict):
                return await self._respond(send, 409, {"error": "proposal_baseline_unavailable"})
            current = await asyncio.to_thread(self.dashboard.snapshot)
            current_metrics = {
                "workforce": current.get("workforce", {}),
                "tasks": current.get("tasks", {}),
                "performance": current.get("performance", {}),
            }
            evaluation = evaluate_improvement_snapshots(baseline, current_metrics)
            changes = {}
            for section in ("workforce", "tasks", "performance"):
                before = baseline.get(section, {})
                after = current_metrics[section]
                section_changes = {}
                if isinstance(before, dict) and isinstance(after, dict):
                    for key, old_value in before.items():
                        new_value = after.get(key)
                        if (type(old_value) in (int, float) and type(new_value) in (int, float)):
                            section_changes[key] = {
                                "baseline": old_value,
                                "current": new_value,
                                "delta": new_value - old_value,
                            }
                changes[section] = section_changes
        except TaskNotFound:
            return await self._respond(send, 404, {"error": "proposal_not_found"})
        except Exception:
            return await self._respond(send, 503, {"error": "improvement_comparison_unavailable"})
        return await self._respond(send, 200, {
            "proposal_id": proposal_id,
            "proposal_state": proposal["state"],
            "baseline_captured_at": baseline.get("captured_at"),
            "comparison_captured_at": self.dashboard.task_engine.clock(),
            "baseline_scope": baseline.get("scope"),
            "changes": changes,
            "evaluation": evaluation,
            "interpretation": "Descriptive snapshot differences only; they do not establish causation or intervention effectiveness.",
        })

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

    async def _read_experiment_json(self, scope, receive, send):
        headers = {key.lower(): value for key, value in scope.get("headers", []) if isinstance(key, bytes) and isinstance(value, bytes)}
        if headers.get(b"content-type", b"").split(b";", 1)[0].strip().lower() != b"application/json":
            await self._respond(send, 415, {"error": "application_json_required"})
            return None
        body = bytearray()
        more_body = True
        while more_body:
            message = await receive()
            if message.get("type") != "http.request":
                continue
            chunk = message.get("body", b"")
            if not isinstance(chunk, bytes):
                await self._respond(send, 400, {"error": "invalid_request_body"})
                return None
            body.extend(chunk)
            if len(body) > _MAX_IMPROVEMENT_BODY_BYTES:
                await self._respond(send, 413, {"error": "request_body_too_large"})
                return None
            more_body = bool(message.get("more_body", False))
        try:
            return json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            await self._respond(send, 400, {"error": "invalid_json"})
            return None

    async def _create_workforce_experiment(self, scope, receive, send):
        principal = await self._principal(scope)
        if principal is None:
            return await self._respond(send, 401, {"error": "authentication_required"})
        user, _ = principal
        if user.role not in {"admin", "operator"}:
            return await self._respond(send, 403, {"error": "workforce_access_denied"})
        payload = await self._read_experiment_json(scope, receive, send)
        if payload is None:
            return
        if not isinstance(payload, dict) or set(payload) != {"experiment_id", "proposal_id", "design"}:
            return await self._respond(send, 400, {"error": "invalid_experiment_request"})
        if not isinstance(payload["experiment_id"], str) or not isinstance(payload["proposal_id"], str) or not isinstance(payload["design"], dict):
            return await self._respond(send, 400, {"error": "invalid_experiment_request"})
        try:
            linked_proposal = await asyncio.to_thread(
                self.dashboard.task_engine.get_improvement_proposal, payload["proposal_id"],
            )
        except TaskNotFound:
            return await self._respond(send, 404, {"error": "proposal_not_found"})
        except Exception:
            return await self._respond(send, 503, {"error": "experiment_unavailable"})
        if linked_proposal.get("state") != "approved":
            return await self._respond(send, 409, {"error": "experiment_conflict"})
        try:
            result = await asyncio.to_thread(
                self.dashboard.task_engine.create_workforce_experiment,
                payload["experiment_id"], payload["proposal_id"], payload["design"],
            )
        except TaskConflict:
            return await self._respond(send, 409, {"error": "experiment_conflict"})
        except TaskNotFound:
            return await self._respond(send, 404, {"error": "proposal_not_found"})
        except (TypeError, ValueError):
            return await self._respond(send, 400, {"error": "invalid_experiment_request"})
        except Exception:
            return await self._respond(send, 503, {"error": "experiment_unavailable"})
        return await self._respond(send, 201 if result.get("created") else 200, result)

    async def _record_workforce_experiment_delivery(self, path, scope, receive, send):
        principal = await self._principal(scope)
        if principal is None:
            return await self._respond(send, 401, {"error": "authentication_required"})
        user, _ = principal
        if user.role not in {"admin", "operator"}:
            return await self._respond(send, 403, {"error": "workforce_access_denied"})
        payload = await self._read_experiment_json(scope, receive, send)
        if payload is None:
            return
        allowed = {"task_id", "delivery_state", "intervention_version", "evidence_reference"}
        required = {"task_id", "delivery_state"}
        if (not isinstance(payload, dict) or not required.issubset(payload)
                or set(payload) - allowed
                or not isinstance(payload.get("task_id"), str)
                or not isinstance(payload.get("delivery_state"), str)):
            return await self._respond(send, 400, {"error": "invalid_delivery_attestation"})
        if "intervention_version" in payload and payload["intervention_version"] is not None and not isinstance(payload["intervention_version"], str):
            return await self._respond(send, 400, {"error": "invalid_delivery_attestation"})
        if "evidence_reference" in payload and payload["evidence_reference"] is not None and not isinstance(payload["evidence_reference"], str):
            return await self._respond(send, 400, {"error": "invalid_delivery_attestation"})
        experiment_id = path.split("/")[-2]
        try:
            result = await asyncio.to_thread(
                self.dashboard.task_engine.record_workforce_experiment_delivery,
                experiment_id, payload["task_id"], payload["delivery_state"],
                actor_id=user.user_id,
                intervention_version=payload.get("intervention_version"),
                evidence_reference=payload.get("evidence_reference"),
            )
        except TaskConflict:
            return await self._respond(send, 409, {"error": "delivery_attestation_conflict"})
        except TaskNotFound:
            return await self._respond(send, 404, {"error": "experiment_assignment_not_found"})
        except (TypeError, ValueError):
            return await self._respond(send, 400, {"error": "invalid_delivery_attestation"})
        except Exception:
            return await self._respond(send, 503, {"error": "experiment_delivery_unavailable"})
        return await self._respond(send, 201 if result.get("created") else 200, result)

    async def _assign_workforce_experiment_task(self, path, scope, receive, send):
        principal = await self._principal(scope)
        if principal is None:
            return await self._respond(send, 401, {"error": "authentication_required"})
        user, _ = principal
        if user.role not in {"admin", "operator"}:
            return await self._respond(send, 403, {"error": "workforce_access_denied"})
        payload = await self._read_experiment_json(scope, receive, send)
        if payload is None:
            return
        if not isinstance(payload, dict) or set(payload) != {"task_id"} or not isinstance(payload["task_id"], str):
            return await self._respond(send, 400, {"error": "invalid_assignment_request"})
        experiment_id = path.split("/")[-2]
        try:
            result = await asyncio.to_thread(
                self.dashboard.task_engine.assign_workforce_experiment_task,
                experiment_id, payload["task_id"],
            )
        except TaskConflict:
            return await self._respond(send, 409, {"error": "experiment_assignment_conflict"})
        except TaskNotFound:
            return await self._respond(send, 404, {"error": "experiment_or_task_not_found"})
        except (TypeError, ValueError):
            return await self._respond(send, 400, {"error": "invalid_assignment_request"})
        except Exception:
            return await self._respond(send, 503, {"error": "experiment_assignment_unavailable"})
        return await self._respond(send, 201 if result.get("created") else 200, {
            **result, "intervention_executed": False,
            "interpretation": "Assignment metadata only; no treatment was applied to the task.",
        })

    async def _transition_workforce_experiment(self, path, scope, receive, send):
        principal = await self._principal(scope)
        if principal is None:
            return await self._respond(send, 401, {"error": "authentication_required"})
        user, _ = principal
        if user.role not in {"admin", "operator"}:
            return await self._respond(send, 403, {"error": "workforce_access_denied"})
        payload = await self._read_experiment_json(scope, receive, send)
        if payload is None:
            return
        allowed = {"expected_state", "new_state", "decision_note"}
        if not isinstance(payload, dict) or not {"expected_state", "new_state"}.issubset(payload) or set(payload) - allowed:
            return await self._respond(send, 400, {"error": "invalid_experiment_transition"})
        if not isinstance(payload["expected_state"], str) or not isinstance(payload["new_state"], str):
            return await self._respond(send, 400, {"error": "invalid_experiment_transition"})
        note = payload.get("decision_note")
        if note is not None and not isinstance(note, str):
            return await self._respond(send, 400, {"error": "invalid_experiment_transition"})
        experiment_id = path.split("/")[-2]
        try:
            result = await asyncio.to_thread(
                self.dashboard.task_engine.transition_workforce_experiment,
                experiment_id, expected_state=payload["expected_state"],
                new_state=payload["new_state"], actor_id=user.user_id, decision_note=note,
            )
        except TaskConflict:
            return await self._respond(send, 409, {"error": "experiment_state_conflict"})
        except TaskNotFound:
            return await self._respond(send, 404, {"error": "experiment_not_found"})
        except (TypeError, ValueError):
            return await self._respond(send, 400, {"error": "invalid_experiment_transition"})
        except Exception:
            return await self._respond(send, 503, {"error": "experiment_unavailable"})
        return await self._respond(send, 200, result)

    async def _create_knowledge_revision(self, path, scope, receive, send):
        principal = await self._principal(scope)
        if principal is None:
            return await self._respond(send, 401, {"error": "authentication_required"})
        user, _ = principal
        if user.role not in {"admin", "operator"}:
            return await self._respond(send, 403, {"error": "workforce_access_denied"})
        headers = {key.lower(): value for key, value in scope.get("headers", []) if isinstance(key, bytes) and isinstance(value, bytes)}
        if headers.get(b"content-type", b"").split(b";", 1)[0].strip().lower() != b"application/json":
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
            if len(body) > 12_288:
                return await self._respond(send, 413, {"error": "request_body_too_large"})
            more_body = bool(message.get("more_body", False))
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return await self._respond(send, 400, {"error": "invalid_json"})
        if (not isinstance(payload, dict) or set(payload) != {"revision_id", "knowledge"}
                or not isinstance(payload.get("revision_id"), str)
                or not isinstance(payload.get("knowledge"), dict)):
            return await self._respond(send, 400, {"error": "invalid_knowledge_revision"})
        knowledge_id = path.split("/")[-2]
        try:
            result = await asyncio.to_thread(
                self.dashboard.task_engine.create_knowledge_revision,
                knowledge_id, payload["revision_id"], payload["knowledge"],
            )
        except TaskConflict:
            return await self._respond(send, 409, {"error": "knowledge_revision_conflict"})
        except TaskNotFound:
            return await self._respond(send, 404, {"error": "knowledge_not_found"})
        except (TypeError, ValueError):
            return await self._respond(send, 400, {"error": "invalid_knowledge_revision"})
        except Exception:
            return await self._respond(send, 503, {"error": "knowledge_store_unavailable"})
        return await self._respond(send, 201 if result.get("created") else 200, result)

    async def _transition_knowledge_revision(self, path, scope, receive, send):
        principal = await self._principal(scope)
        if principal is None:
            return await self._respond(send, 401, {"error": "authentication_required"})
        user, _ = principal
        if user.role not in {"admin", "operator"}:
            return await self._respond(send, 403, {"error": "workforce_access_denied"})
        headers = {key.lower(): value for key, value in scope.get("headers", []) if isinstance(key, bytes) and isinstance(value, bytes)}
        if headers.get(b"content-type", b"").split(b";", 1)[0].strip().lower() != b"application/json":
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
            if len(body) > 4096:
                return await self._respond(send, 413, {"error": "request_body_too_large"})
            more_body = bool(message.get("more_body", False))
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return await self._respond(send, 400, {"error": "invalid_json"})
        allowed = {"expected_state", "new_state", "review_note"}
        if (not isinstance(payload, dict) or not {"expected_state", "new_state"}.issubset(payload)
                or set(payload) - allowed or not isinstance(payload["expected_state"], str)
                or not isinstance(payload["new_state"], str)
                or (payload.get("review_note") is not None and not isinstance(payload["review_note"], str))):
            return await self._respond(send, 400, {"error": "invalid_knowledge_revision_transition"})
        revision_id = path.split("/")[-2]
        try:
            result = await asyncio.to_thread(
                self.dashboard.task_engine.transition_knowledge_revision,
                revision_id, expected_state=payload["expected_state"],
                new_state=payload["new_state"], actor_id=user.user_id,
                review_note=payload.get("review_note"),
            )
        except TaskConflict:
            return await self._respond(send, 409, {"error": "knowledge_revision_conflict"})
        except TaskNotFound:
            return await self._respond(send, 404, {"error": "knowledge_revision_not_found"})
        except (TypeError, ValueError):
            return await self._respond(send, 400, {"error": "invalid_knowledge_revision_transition"})
        except Exception:
            return await self._respond(send, 503, {"error": "knowledge_store_unavailable"})
        return await self._respond(send, 200, result)

    async def _transition_knowledge(self, path, scope, receive, send):
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
            if len(body) > 4096:
                return await self._respond(send, 413, {"error": "request_body_too_large"})
            more_body = bool(message.get("more_body", False))
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return await self._respond(send, 400, {"error": "invalid_json"})
        allowed = {"expected_state", "new_state", "review_note"}
        if (not isinstance(payload, dict)
                or not {"expected_state", "new_state"}.issubset(payload)
                or set(payload) - allowed
                or not isinstance(payload["expected_state"], str)
                or not isinstance(payload["new_state"], str)
                or (payload.get("review_note") is not None and not isinstance(payload["review_note"], str))):
            return await self._respond(send, 400, {"error": "invalid_knowledge_transition"})
        knowledge_id = path.split("/")[-2]
        try:
            result = await asyncio.to_thread(
                self.dashboard.task_engine.transition_knowledge,
                knowledge_id,
                expected_state=payload["expected_state"],
                new_state=payload["new_state"],
                actor_id=user.user_id,
                review_note=payload.get("review_note"),
            )
        except TaskConflict:
            return await self._respond(send, 409, {"error": "knowledge_state_conflict"})
        except TaskNotFound:
            return await self._respond(send, 404, {"error": "knowledge_not_found"})
        except (TypeError, ValueError):
            return await self._respond(send, 400, {"error": "invalid_knowledge_transition"})
        except Exception:
            return await self._respond(send, 503, {"error": "knowledge_store_unavailable"})
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
