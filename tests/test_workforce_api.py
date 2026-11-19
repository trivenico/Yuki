import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from api.workforce import AuthenticatedWorkforceDashboardAPI
from core.auth import BuiltInAuthService
from core.approval_audit import SQLiteApprovalAuditLog
from core.pending_approvals import SQLitePendingApprovalStore
from core.durable_task_engine import DurableTaskEngine
from core.workforce import AgentRegistry, default_workforce
from core.workforce_dashboard import WorkforceDashboard
from core.planner import Planner
from core.workforce_ceo import WorkforceCEO


class _Downstream:
    async def __call__(self, scope, receive, send):
        await send({"type": "http.response.start", "status": 404, "headers": []})
        await send({"type": "http.response.body", "body": b"not found"})


class WorkforceDashboardAPITests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        root = Path(self.tempdir.name)
        self.auth = BuiltInAuthService(str(root / "auth.sqlite"), password_iterations=100_000)
        self.auth.bootstrap_admin("admin-user", "a-secure-test-password")
        self.admin_session = self.auth.authenticate("admin-user", "a-secure-test-password")
        self.auth.create_account("regular-user", "another-secure-password")
        self.user_session = self.auth.authenticate("regular-user", "another-secure-password")
        self.engine = DurableTaskEngine(str(root / "tasks.sqlite"))
        registry = AgentRegistry(default_workforce())
        self.dashboard = WorkforceDashboard(registry=registry, task_engine=self.engine)
        self.ceo = WorkforceCEO(
            planner=Planner(max_steps=5), registry=registry, task_engine=self.engine
        )
        self.pending = SQLitePendingApprovalStore(str(root / "pending.sqlite"))
        self.audit = SQLiteApprovalAuditLog(str(root / "approval-audit.sqlite"))
        self.app = AuthenticatedWorkforceDashboardAPI(
            _Downstream(), self.auth, self.dashboard,
            pending_approvals=self.pending, approval_audit=self.audit,
            workforce_ceo=self.ceo,
        )

    def tearDown(self):
        self.engine.close()
        self.tempdir.cleanup()

    def request(self, path, *, method="GET", token=None, query=b"", body=b"", extra_headers=None):
        async def run():
            messages = []
            received = False
            async def receive():
                nonlocal received
                if received:
                    return {"type": "http.request", "body": b"", "more_body": False}
                received = True
                return {"type": "http.request", "body": body, "more_body": False}
            async def send(message):
                messages.append(message)
            headers = []
            if token:
                headers.append((b"authorization", f"Bearer {token}".encode()))
            if extra_headers:
                headers.extend(extra_headers)
            await self.app({
                "type": "http", "method": method, "path": path,
                "headers": headers, "query_string": query,
            }, receive, send)
            body = messages[1].get("body", b"")
            try:
                decoded = json.loads(body)
            except (json.JSONDecodeError, UnicodeDecodeError):
                decoded = body.decode("utf-8", errors="replace")
            return messages[0]["status"], decoded
        return asyncio.run(run())

    def _seed_knowledge_candidate(self):
        goal = self.engine.create_goal()
        task = self.engine.submit(
            goal, "workforce.task", {}, idempotency_key="api-knowledge-source",
            max_attempts=1,
        )
        claimed = self.engine.claim("api-knowledge-worker")
        self.engine.complete(task["id"], "api-knowledge-worker", {
            "ok": True,
            "review_required": True,
            "review_evidence": {
                "reviewer_id": "independent-reviewer",
                "status": "approved",
                "approved": True,
            },
        })
        return self.engine.create_knowledge_candidate(
            "api-knowledge-1", task["id"], {
                "title": "Keep retries bounded",
                "lesson": "Use stable idempotency keys for replayable task dispatch.",
                "domain": "workforce",
                "tags": ["reliability"],
            },
        )

    def test_knowledge_api_requires_auth_and_only_returns_verified_records(self):
        candidate = self._seed_knowledge_candidate()
        status, body = self.request("/v1/workforce/knowledge")
        self.assertEqual(status, 401)
        status, body = self.request(
            "/v1/workforce/knowledge", token=self.user_session.token,
        )
        self.assertEqual(status, 403)
        status, body = self.request(
            "/v1/workforce/knowledge", token=self.admin_session.token,
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["knowledge"], [])
        self.assertEqual(body["visibility"], "verified_only")
        status, body = self.request(
            "/v1/workforce/knowledge/api-knowledge-1",
            token=self.admin_session.token,
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["state"], "candidate")
        self.assertEqual(body["source_task_id"], candidate["source_task_id"])

    def test_knowledge_revision_api_requires_role_and_human_approval(self):
        self._seed_knowledge_candidate()
        self.engine.transition_knowledge(
            "api-knowledge-1", expected_state="candidate", new_state="verified",
            actor_id="initial-curator",
        )
        revised = {
            "title": "Keep retries bounded",
            "lesson": "Use stable idempotency keys and bounded retry budgets for replayable dispatch.",
            "domain": "workforce",
            "tags": ["reliability"],
        }
        payload = {"revision_id": "api-revision-2", "knowledge": revised}
        status, body = self.request(
            "/v1/workforce/knowledge/api-knowledge-1/revisions",
            method="POST", token=self.user_session.token,
            body=json.dumps(payload).encode(),
            extra_headers=[(b"content-type", b"application/json")],
        )
        self.assertEqual(status, 403)
        status, body = self.request(
            "/v1/workforce/knowledge/api-knowledge-1/revisions",
            method="POST", token=self.admin_session.token,
            body=json.dumps(payload).encode(),
            extra_headers=[(b"content-type", b"application/json")],
        )
        self.assertEqual(status, 201)
        self.assertEqual(body["state"], "proposed")
        status, body = self.request(
            "/v1/workforce/knowledge/api-knowledge-1/revisions",
            token=self.admin_session.token,
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["count"], 2)
        self.assertEqual(body["revisions"][0]["version"], 1)
        self.assertEqual(body["revisions"][0]["state"], "approved")
        self.assertEqual(body["revisions"][1]["version"], 2)
        self.assertEqual(body["revisions"][1]["state"], "proposed")
        status, current = self.request(
            "/v1/workforce/knowledge/api-knowledge-1",
            token=self.admin_session.token,
        )
        self.assertEqual(current["knowledge"]["lesson"], "Use stable idempotency keys for replayable task dispatch.")
        transition = {
            "expected_state": "proposed",
            "new_state": "approved",
            "review_note": "Compared the revision against the source evidence.",
        }
        status, body = self.request(
            "/v1/workforce/knowledge-revisions/api-revision-2/transition",
            method="POST", token=self.admin_session.token,
            body=json.dumps(transition).encode(),
            extra_headers=[(b"content-type", b"application/json")],
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["reviewed_by"], "admin-user")
        self.assertEqual(body["state"], "approved")
        status, current = self.request(
            "/v1/workforce/knowledge/api-knowledge-1",
            token=self.admin_session.token,
        )
        self.assertEqual(current["knowledge"], revised)

    def test_knowledge_api_review_is_actor_attributed_and_optimistic(self):
        self._seed_knowledge_candidate()
        payload = {
            "expected_state": "candidate",
            "new_state": "verified",
            "review_note": "Checked source evidence and confirmed lesson.",
        }
        status, body = self.request(
            "/v1/workforce/knowledge/api-knowledge-1/transition",
            method="POST", token=self.admin_session.token,
            body=json.dumps(payload).encode(),
            extra_headers=[(b"content-type", b"application/json")],
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["state"], "verified")
        self.assertEqual(body["reviewed_by"], "admin-user")
        status, body = self.request(
            "/v1/workforce/knowledge", token=self.admin_session.token,
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["count"], 1)
        stale = self.request(
            "/v1/workforce/knowledge/api-knowledge-1/transition",
            method="POST", token=self.admin_session.token,
            body=json.dumps(payload).encode(),
            extra_headers=[(b"content-type", b"application/json")],
        )
        self.assertEqual(stale[0], 409)

    def test_knowledge_api_rejects_invalid_transition_and_query(self):
        self._seed_knowledge_candidate()
        status, body = self.request(
            "/v1/workforce/knowledge?all=true", token=self.admin_session.token,
        )
        self.assertEqual(status, 400)
        status, body = self.request(
            "/v1/workforce/knowledge/api-knowledge-1/transition",
            method="POST", token=self.admin_session.token,
            body=json.dumps({
                "expected_state": "candidate",
                "new_state": "deleted",
            }).encode(),
            extra_headers=[(b"content-type", b"application/json")],
        )
        self.assertEqual(status, 409)

    def test_requires_authentication_and_restricts_regular_users(self):
        status, body = self.request("/v1/workforce/dashboard")
        self.assertEqual(status, 401)
        self.assertEqual(body["error"], "authentication_required")
        status, body = self.request(
            "/v1/workforce/dashboard", token=self.user_session.token
        )
        self.assertEqual(status, 403)
        self.assertEqual(body["error"], "workforce_access_denied")

    def test_admin_can_read_dashboard_and_paginated_directory(self):
        status, body = self.request(
            "/v1/workforce/dashboard", token=self.admin_session.token
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["workforce"]["total_employees"], 100)
        status, body = self.request(
            "/v1/workforce/employees", token=self.admin_session.token,
            query=b"department=Engineering&page=2&page_size=3",
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["total"], 10)
        self.assertEqual(len(body["employees"]), 3)
        self.assertEqual(body["page"], 2)

    def test_department_detail_and_unknown_department(self):
        status, body = self.request(
            "/v1/workforce/departments", token=self.admin_session.token,
            query=b"name=Engineering",
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["department"]["employees"], 10)
        status, body = self.request(
            "/v1/workforce/departments", token=self.admin_session.token,
            query=b"name=Unknown",
        )
        self.assertEqual(status, 404)

    def test_goal_detail_returns_bounded_task_timeline(self):
        goal_id = self.engine.create_goal()
        employee = self.dashboard.registry.recommend({"python"}, limit=1)[0]
        task = self.engine.submit(
            goal_id, "workforce.task",
            {"assigned_employee_id": employee.employee_id, "description": "Run tests"},
            idempotency_key="timeline-task", max_attempts=1,
        )
        claimed = self.engine.claim("timeline-worker", task_type="workforce.task")
        self.engine.complete(claimed["id"], "timeline-worker", {"ok": True})
        status, body = self.request(
            "/v1/workforce/goals/" + goal_id, token=self.admin_session.token,
            query=b"event_limit=2",
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["goal"]["goal_id"], goal_id)
        self.assertEqual(body["tasks"][0]["task_id"], task["id"])
        self.assertEqual(body["tasks"][0]["employee_id"], employee.employee_id)
        self.assertEqual(len(body["events"]), 2)
        self.assertTrue(body["has_more_events"])
        self.assertNotIn("detail", body["events"][0])

    def test_goal_detail_rejects_invalid_query_and_unknown_goal(self):
        status, _ = self.request("/v1/workforce/goals/unknown", token=self.admin_session.token)
        self.assertEqual(status, 404)
        status, _ = self.request(
            "/v1/workforce/goals/unknown", token=self.admin_session.token,
            query=b"event_limit=999",
        )
        self.assertEqual(status, 400)

    def test_approval_inbox_and_audit_are_admin_operator_read_only(self):
        item = self.pending.put(
            "notes.write", {"text": "private"},
            {"skill_id": "notes.write", "arguments": {"text": "private"}},
            actor=self.admin_session.user.user_id, session_id=self.admin_session.token,
        )
        self.audit.append(
            event_type="requested", pending_id=item.pending_id,
            actor="owner", skill_id="notes.write",
        )
        status, body = self.request(
            "/v1/workforce/approvals", token=self.admin_session.token
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["count"], 1)
        self.assertEqual(body["pending"][0]["pending_id"], item.pending_id)
        self.assertNotIn("arguments", body["pending"][0])
        second_session = self.auth.authenticate("admin-user", "a-secure-test-password")
        status, other_session_body = self.request("/v1/workforce/approvals", token=second_session.token)
        self.assertEqual(status, 200)
        self.assertEqual(other_session_body["count"], 0)
        status, body = self.request(
            "/v1/workforce/approvals/audit", token=self.admin_session.token
        )
        self.assertEqual(status, 200)
        self.assertTrue(body["chain_valid"])
        self.assertEqual(body["events"][0]["event_type"], "requested")
        self.assertNotIn("pending_id", body["events"][0])
        status, _ = self.request(
            "/v1/workforce/approvals", token=self.user_session.token
        )
        self.assertEqual(status, 403)

    def test_approval_preview_is_redacted_and_session_scoped(self):
        item = self.pending.put(
            "notes.write",
            {"text": "private", "api_token": "super-secret-token"},
            {"skill_id": "notes.write", "arguments": {
                "text": "private", "api_token": "super-secret-token",
                "nested": {"password": "hidden", "count": 3},
            }},
            actor=self.admin_session.user.user_id, session_id=self.admin_session.token,
        )
        path = "/v1/workforce/approvals/" + item.pending_id
        status, body = self.request(path, token=self.admin_session.token)
        self.assertEqual(status, 200)
        self.assertEqual(body["execution"], "not_executed")
        self.assertRegex(body["confirmation_digest"], r"^[0-9a-f]{64}$")
        self.assertEqual(body["risk"], "explicit_confirmation_required")
        self.assertEqual(body["action"]["arguments"]["api_token"], "[REDACTED]")
        self.assertEqual(body["action"]["arguments"]["nested"]["password"], "[REDACTED]")
        self.assertEqual(body["action"]["arguments"]["nested"]["count"], 3)
        self.assertNotIn("super-secret-token", json.dumps(body))
        second_session = self.auth.authenticate("admin-user", "a-secure-test-password")
        status, _ = self.request(path, token=second_session.token)
        self.assertEqual(status, 404)
        status, _ = self.request(path, token=self.user_session.token)
        self.assertEqual(status, 403)

    def test_approval_inbox_requires_explicit_store_configuration(self):
        app = AuthenticatedWorkforceDashboardAPI(_Downstream(), self.auth, self.dashboard)
        async def run():
            messages = []
            async def receive():
                return {"type": "http.request", "body": b"", "more_body": False}
            async def send(message):
                messages.append(message)
            await app({
                "type": "http", "method": "GET", "path": "/v1/workforce/approvals",
                "headers": [(b"authorization", f"Bearer {self.admin_session.token}".encode())],
                "query_string": b"",
            }, receive, send)
            return messages
        messages = asyncio.run(run())
        self.assertEqual(messages[0]["status"], 503)

    def test_rejects_bad_methods_queries_and_pagination(self):
        status, _ = self.request(
            "/v1/workforce/dashboard", method="POST", token=self.admin_session.token
        )
        self.assertEqual(status, 405)
        for query in (b"unexpected=yes", b"max_goals=9999", b"max_goals=1&max_goals=2"):
            status, _ = self.request(
                "/v1/workforce/dashboard", token=self.admin_session.token, query=query
            )
            self.assertEqual(status, 400)
        status, _ = self.request(
            "/v1/workforce/employees", token=self.admin_session.token, query=b"page=0"
        )
        self.assertEqual(status, 400)
        status, _ = self.request(
            "/v1/workforce/goals/unknown", token=self.admin_session.token,
            query=b"after_event_id=10000",
        )
        self.assertEqual(status, 404)

    def test_authenticated_goal_submission_is_idempotent(self):
        payload = json.dumps({"goal": "Research the market and report findings"}).encode()
        headers = [
            (b"content-type", b"application/json"),
            (b"idempotency-key", b"market-plan-001"),
        ]
        status, first = self.request(
            "/v1/workforce/goals", method="POST", token=self.admin_session.token,
            body=payload, extra_headers=headers,
        )
        self.assertEqual(status, 202)
        self.assertTrue(first["created"])
        self.assertEqual(first["execution"], "queued")
        self.assertEqual(first["report"]["counts"], {"queued": 2})
        goal_id = first["goal_id"]
        stored = self.engine.get_goal(goal_id)
        self.assertEqual(stored["owner_id"], self.admin_session.user.user_id)
        task_ids = {task["id"] for task in self.engine.list_tasks(goal_id)}
        status, replay = self.request(
            "/v1/workforce/goals", method="POST", token=self.admin_session.token,
            body=payload, extra_headers=headers,
        )
        self.assertEqual(status, 202)
        self.assertTrue(replay["replayed"])
        self.assertEqual(replay["goal_id"], goal_id)
        self.assertEqual({task["id"] for task in self.engine.list_tasks(goal_id)}, task_ids)
        self.assertEqual(len(self.engine.list_tasks(goal_id)), 2)

    def test_goal_submission_rejects_conflicting_key_and_invalid_requests(self):
        headers = [
            (b"content-type", b"application/json"),
            (b"idempotency-key", b"same-key"),
        ]
        status, _ = self.request(
            "/v1/workforce/goals", method="POST", token=self.admin_session.token,
            body=b'{"goal":"Research the market"}', extra_headers=headers,
        )
        self.assertEqual(status, 202)
        status, body = self.request(
            "/v1/workforce/goals", method="POST", token=self.admin_session.token,
            body=b'{"goal":"Build a product"}', extra_headers=headers,
        )
        self.assertEqual(status, 409)
        self.assertEqual(body["error"], "idempotency_conflict")
        for body_bytes, extra, expected in (
            (b'{"goal":""}', headers, 400),
            (b'{"goal":"x"}', [(b"content-type", b"application/json")], 400),
            (b'{"goal":"x"}', [(b"content-type", b"text/plain"), (b"idempotency-key", b"x")], 415),
        ):
            status, _ = self.request(
                "/v1/workforce/goals", method="POST", token=self.admin_session.token,
                body=body_bytes, extra_headers=extra,
            )
            self.assertEqual(status, expected)

    def test_goal_submission_enforces_streamed_body_limit(self):
        headers = [
            (b"content-type", b"application/json"),
            (b"idempotency-key", b"oversized"),
        ]
        status, body = self.request(
            "/v1/workforce/goals", method="POST", token=self.admin_session.token,
            body=b" " * 16_385, extra_headers=headers,
        )
        self.assertEqual(status, 413)
        self.assertEqual(body["error"], "request_body_too_large")

    def test_goal_submission_requires_role_and_explicit_runtime(self):
        status, _ = self.request(
            "/v1/workforce/goals", method="POST", token=self.user_session.token,
            body=b'{"goal":"Research"}',
            extra_headers=[(b"content-type", b"application/json"), (b"idempotency-key", b"nope")],
        )
        self.assertEqual(status, 403)
        app = AuthenticatedWorkforceDashboardAPI(_Downstream(), self.auth, self.dashboard)
        async def run():
            messages = []
            async def receive():
                return {"type": "http.request", "body": b'{"goal":"Research"}', "more_body": False}
            async def send(message):
                messages.append(message)
            await app({
                "type": "http", "method": "POST", "path": "/v1/workforce/goals",
                "headers": [
                    (b"authorization", f"Bearer {self.admin_session.token}".encode()),
                    (b"content-type", b"application/json"), (b"idempotency-key", b"disabled"),
                ], "query_string": b"",
            }, receive, send)
            return messages
        self.assertEqual(asyncio.run(run())[0]["status"], 503)


    def test_improvement_proposals_are_authenticated_and_read_only_on_get(self):
        self.engine.create_improvement_proposal("capacity-review", {
            "category": "department_backlog", "recommendation": "Review queue priorities",
        })
        status, body = self.request("/v1/workforce/improvement-proposals")
        self.assertEqual(status, 401)
        status, body = self.request("/v1/workforce/improvement-proposals", token=self.user_session.token)
        self.assertEqual(status, 403)
        status, body = self.request("/v1/workforce/improvement-proposals", token=self.admin_session.token)
        self.assertEqual(status, 200)
        self.assertEqual(body["count"], 1)
        self.assertEqual(body["proposals"][0]["state"], "proposed")
        status, detail = self.request(
            "/v1/workforce/improvement-proposals/capacity-review",
            token=self.admin_session.token,
        )
        self.assertEqual(status, 200)
        self.assertEqual(detail["proposal"]["category"], "department_backlog")
        self.assertEqual(detail["events"][0]["actor_id"], "system")

    def test_improvement_proposal_transition_derives_actor_and_never_executes(self):
        self.engine.create_improvement_proposal("review-queue", {
            "category": "department_backlog", "recommendation": "Review queue priorities",
        })
        path = "/v1/workforce/improvement-proposals/review-queue/transition"
        headers = [(b"content-type", b"application/json")]
        status, denied = self.request(
            path, method="POST", token=self.user_session.token,
            body=b'{"expected_state":"proposed","new_state":"approved"}',
            extra_headers=headers,
        )
        self.assertEqual(status, 403)
        status, approved = self.request(
            path, method="POST", token=self.admin_session.token,
            body=json.dumps({
                "expected_state": "proposed", "new_state": "approved",
                "decision_note": "Reviewed by an authorized operator",
                "actor_id": "forged-actor",
            }).encode(),
            extra_headers=headers,
        )
        self.assertEqual(status, 400)
        status, approved = self.request(
            path, method="POST", token=self.admin_session.token,
            body=json.dumps({
                "expected_state": "proposed", "new_state": "approved",
                "decision_note": "Reviewed by an authorized operator",
            }).encode(),
            extra_headers=headers,
        )
        self.assertEqual(status, 200)
        self.assertEqual(approved["state"], "approved")
        self.assertEqual(approved["decided_by"], self.admin_session.user.user_id)
        self.assertEqual(approved["events"][-1]["actor_id"], self.admin_session.user.user_id)
        self.assertEqual(approved["events"][-1]["event_type"], "proposal.approved")
        self.assertIsNone(approved["outcome"])
        self.assertNotIn("execution", approved)

    def test_improvement_proposal_transition_checks_expected_state_and_completion_outcome(self):
        self.engine.create_improvement_proposal("outcome-review", {"recommendation": "Review"})
        path = "/v1/workforce/improvement-proposals/outcome-review/transition"
        headers = [(b"content-type", b"application/json")]
        status, body = self.request(
            path, method="POST", token=self.admin_session.token,
            body=b'{"expected_state":"approved","new_state":"completed","outcome":{"observed":true}}',
            extra_headers=headers,
        )
        self.assertEqual(status, 409)
        status, body = self.request(
            path, method="POST", token=self.admin_session.token,
            body=b'{"expected_state":"proposed","new_state":"approved"}',
            extra_headers=headers,
        )
        self.assertEqual(status, 200)
        status, body = self.request(
            path, method="POST", token=self.admin_session.token,
            body=b'{"expected_state":"approved","new_state":"completed"}',
            extra_headers=headers,
        )
        self.assertEqual(status, 400)
        status, body = self.request(
            path, method="POST", token=self.admin_session.token,
            body=b'{"expected_state":"approved","new_state":"completed","outcome":{"observed":true}}',
            extra_headers=headers,
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["state"], "completed")
        self.assertEqual(body["outcome"], {"observed": True})

    def test_improvement_proposal_comparison_is_authenticated_and_descriptive(self):
        self.engine.create_improvement_proposal("baseline-compare", {
            "recommendation": "Review workforce",
            "baseline": {
                "captured_at": 100.0,
                "scope": "bounded_workforce_dashboard_snapshot",
                "workforce": {"total_employees": 0},
                "tasks": {"total": 0},
                "performance": {"failed_tasks": 0},
            },
        })
        path = "/v1/workforce/improvement-proposals/baseline-compare/comparison"
        status, body = self.request(path)
        self.assertEqual(status, 401)
        status, body = self.request(path, token=self.user_session.token)
        self.assertEqual(status, 403)
        status, body = self.request(path, token=self.admin_session.token)
        self.assertEqual(status, 200)
        self.assertEqual(body["proposal_id"], "baseline-compare")
        self.assertEqual(body["baseline_captured_at"], 100.0)
        self.assertEqual(body["baseline_scope"], "bounded_workforce_dashboard_snapshot")
        metric = body["changes"]["workforce"]["total_employees"]
        self.assertEqual(metric["delta"], metric["current"] - metric["baseline"])
        self.assertIn("do not establish causation", body["interpretation"])
        self.assertEqual(body["evaluation"]["status"], "insufficient_observations")
        self.assertTrue(body["evaluation"]["human_review_required"])
        self.assertFalse(body["evaluation"]["automatic_action_taken"])
        self.assertIn("do not establish causation", body["evaluation"]["interpretation"])

    def test_recommendation_can_become_idempotent_durable_proposal(self):
        employee = next(item for item in self.dashboard.registry.list_employees() if item.department == "Engineering")
        goal_id = self.engine.create_goal()
        self.engine.submit(
            goal_id, "workforce.task",
            {"assigned_employee_id": employee.employee_id, "description": "Review queued engineering work"},
            idempotency_key="proposal-source-task", max_attempts=1,
        )
        status, snapshot = self.request("/v1/workforce/dashboard", token=self.admin_session.token)
        self.assertEqual(status, 200)
        recommendation = next(
            item for item in snapshot["decision_intelligence"]["improvement_recommendations"]
            if item["category"] == "department_backlog" and item["evidence"]["department"] == "Engineering"
        )
        path = "/v1/workforce/improvement-proposals"
        headers = [(b"content-type", b"application/json")]
        body = json.dumps({"recommendation_id": recommendation["id"]}).encode()
        status, first = self.request(path, method="POST", token=self.admin_session.token, body=body, extra_headers=headers)
        self.assertEqual(status, 201)
        self.assertTrue(first["created"])
        self.assertEqual(first["state"], "proposed")
        self.assertEqual(first["proposal"]["source"], "workforce_decision_intelligence")
        self.assertEqual(first["proposal"]["recommendation_id"], recommendation["id"])
        self.assertTrue(first["proposal"]["requires_human_approval"])
        self.assertFalse(first["proposal"]["automatic_action_taken"])
        baseline = first["proposal"]["baseline"]
        self.assertEqual(baseline["scope"], "bounded_workforce_dashboard_snapshot")
        self.assertEqual(baseline["goal_window"]["max_goals"], 100)
        self.assertGreaterEqual(baseline["goal_window"]["included_goal_count"], 1)
        self.assertIsInstance(baseline["captured_at"], (int, float))
        self.assertIn("queued", baseline["tasks"])
        self.assertIn("independently_verified_successes", baseline["performance"])
        status, replay = self.request(path, method="POST", token=self.admin_session.token, body=body, extra_headers=headers)
        self.assertEqual(status, 200)
        self.assertFalse(replay["created"])
        self.assertEqual(replay["proposal_id"], first["proposal_id"])
        self.assertEqual(len(self.engine.list_improvement_proposals()), 1)

    def test_recommendation_proposal_creation_requires_role_and_exact_schema(self):
        path = "/v1/workforce/improvement-proposals"
        headers = [(b"content-type", b"application/json")]
        status, _ = self.request(path, method="POST", token=self.user_session.token, body=b"{}", extra_headers=headers)
        self.assertEqual(status, 403)
        status, body = self.request(path, method="POST", token=self.admin_session.token, body=b'{"recommendation_id":"missing"}', extra_headers=headers)
        self.assertEqual(status, 404)
        status, body = self.request(path, method="POST", token=self.admin_session.token, body=b'{"recommendation_id":"x","proposal":{}}', extra_headers=headers)
        self.assertEqual(status, 400)
        self.assertEqual(body["error"], "invalid_proposal_request")

    def test_experiment_outcome_report_is_read_only_and_role_restricted(self):
        self.engine.create_improvement_proposal("outcome-api-proposal", {"recommendation": "Measure"})
        self.engine.transition_improvement_proposal(
            "outcome-api-proposal", expected_state="proposed", new_state="approved",
            actor_id=self.admin_session.user.user_id,
        )
        design = {
            "hypothesis": "A checklist reduces failures",
            "intervention": "Human-reviewed checklist",
            "primary_metric": "failed_tasks",
            "preferred_direction": "decrease",
            "design_type": "randomized_controlled",
            "eligible_population": {"description": "Workforce tasks", "task_types": ["workforce.task"]},
            "guardrails": [],
            "evaluation_window_days": 7,
            "minimum_sample_size": 5,
        }
        self.engine.create_workforce_experiment("outcome-api-exp", "outcome-api-proposal", design)
        self.engine.transition_workforce_experiment(
            "outcome-api-exp", expected_state="proposed", new_state="approved",
            actor_id=self.admin_session.user.user_id,
        )
        task = self.engine.submit(self.engine.create_goal(), "workforce.task", {}, idempotency_key="outcome-api-task")
        self.engine.assign_workforce_experiment_task("outcome-api-exp", task["id"])
        path = "/v1/workforce/experiments/outcome-api-exp/outcomes"
        status, _ = self.request(path)
        self.assertEqual(status, 401)
        status, _ = self.request(path, token=self.user_session.token)
        self.assertEqual(status, 403)
        status, report = self.request(path, token=self.admin_session.token)
        self.assertEqual(status, 200)
        self.assertEqual(report["total_assigned"], 1)
        self.assertEqual(report["total_evaluable"], 0)
        self.assertEqual(report["status"], "insufficient_observations")
        self.assertFalse(report["automatic_action"])
        status, _ = self.request(path, token=self.admin_session.token, query=b"unexpected=true")
        self.assertEqual(status, 400)

    def test_experiment_integrity_api_is_authenticated_and_read_only(self):
        self.engine.create_improvement_proposal("integrity-api-proposal", {"recommendation": "Audit"})
        self.engine.transition_improvement_proposal(
            "integrity-api-proposal", expected_state="proposed", new_state="approved",
            actor_id=self.admin_session.user.user_id,
        )
        design = {
            "hypothesis": "A checklist reduces failures",
            "intervention": "Human-reviewed checklist",
            "primary_metric": "failed_tasks",
            "preferred_direction": "decrease",
            "design_type": "randomized_controlled",
            "eligible_population": {"description": "Workforce tasks", "task_types": ["workforce.task"]},
            "guardrails": [],
            "evaluation_window_days": 7,
            "minimum_sample_size": 2,
        }
        self.engine.create_workforce_experiment("integrity-api-exp", "integrity-api-proposal", design)
        self.engine.transition_workforce_experiment(
            "integrity-api-exp", expected_state="proposed", new_state="approved",
            actor_id=self.admin_session.user.user_id,
        )
        task = self.engine.submit(
            self.engine.create_goal(), "workforce.task", {}, idempotency_key="integrity-api-task",
        )
        self.engine.assign_workforce_experiment_task("integrity-api-exp", task["id"])
        path = "/v1/workforce/experiments/integrity-api-exp/integrity"
        status, _ = self.request(path)
        self.assertEqual(status, 401)
        status, _ = self.request(path, token=self.user_session.token)
        self.assertEqual(status, 403)
        status, report = self.request(path, token=self.admin_session.token)
        self.assertEqual(status, 200)
        self.assertEqual(report["assignments_checked"], 1)
        self.assertFalse(report["automatic_action"])
        self.assertIn(report["status"], {"integrity_warnings", "no_detected_integrity_issues"})
        events_path = "/v1/workforce/experiments/integrity-api-exp/execution-events"
        status, events = self.request(events_path, token=self.admin_session.token)
        self.assertEqual(status, 200)
        self.assertEqual(events["execution_events"], [])
        status, _ = self.request(events_path, token=self.user_session.token)
        self.assertEqual(status, 403)
        status, _ = self.request(path, token=self.admin_session.token, query=b"unexpected=true")
        self.assertEqual(status, 400)

    def test_treatment_delivery_attestation_api_is_authenticated_and_idempotent(self):
        self.engine.create_improvement_proposal("delivery-api-proposal", {"recommendation": "Measure"})
        self.engine.transition_improvement_proposal(
            "delivery-api-proposal", expected_state="proposed", new_state="approved",
            actor_id=self.admin_session.user.user_id,
        )
        design = {
            "hypothesis": "A checklist reduces failures",
            "intervention": "Human-reviewed checklist",
            "primary_metric": "failed_tasks",
            "preferred_direction": "decrease",
            "design_type": "randomized_controlled",
            "eligible_population": {"description": "Workforce tasks", "task_types": ["workforce.task"]},
            "guardrails": [],
            "evaluation_window_days": 7,
            "minimum_sample_size": 2,
        }
        self.engine.create_workforce_experiment("delivery-api-exp", "delivery-api-proposal", design)
        self.engine.transition_workforce_experiment(
            "delivery-api-exp", expected_state="proposed", new_state="approved",
            actor_id=self.admin_session.user.user_id,
        )
        treatment_task = None
        control_task = None
        for index in range(20):
            task = self.engine.submit(
                self.engine.create_goal(), "workforce.task", {},
                idempotency_key=f"delivery-api-{index}",
            )
            assignment = self.engine.assign_workforce_experiment_task("delivery-api-exp", task["id"])
            if assignment["assignment"] == "treatment" and treatment_task is None:
                treatment_task = task
            if assignment["assignment"] == "control" and control_task is None:
                control_task = task
            if treatment_task and control_task:
                break
        self.assertIsNotNone(treatment_task)
        path = "/v1/workforce/experiments/delivery-api-exp/deliveries"
        headers = [(b"content-type", b"application/json")]
        body = json.dumps({
            "task_id": treatment_task["id"],
            "delivery_state": "delivered",
            "intervention_version": "checklist-v1",
            "evidence_reference": "review-record-456",
        }).encode()
        status, _ = self.request(path, method="POST", body=body, extra_headers=headers)
        self.assertEqual(status, 401)
        status, _ = self.request(
            path, method="POST", token=self.user_session.token, body=body, extra_headers=headers,
        )
        self.assertEqual(status, 403)
        status, created = self.request(
            path, method="POST", token=self.admin_session.token, body=body, extra_headers=headers,
        )
        self.assertEqual(status, 201)
        self.assertFalse(created["treatment_executed"])
        status, replay = self.request(
            path, method="POST", token=self.admin_session.token, body=body, extra_headers=headers,
        )
        self.assertEqual(status, 200)
        self.assertFalse(replay["created"])
        conflict_body = json.dumps({
            "task_id": treatment_task["id"], "delivery_state": "not_delivered",
        }).encode()
        status, _ = self.request(
            path, method="POST", token=self.admin_session.token,
            body=conflict_body, extra_headers=headers,
        )
        self.assertEqual(status, 409)
        status, listing = self.request(
            path, token=self.admin_session.token,
        )
        self.assertEqual(status, 200)
        self.assertEqual(len(listing["deliveries"]), 1)
        control_body = json.dumps({
            "task_id": control_task["id"], "delivery_state": "delivered",
            "intervention_version": "checklist-v1",
        }).encode()
        status, _ = self.request(
            path, method="POST", token=self.admin_session.token,
            body=control_body, extra_headers=headers,
        )
        self.assertEqual(status, 409)

    def test_experiment_api_is_authenticated_review_gated_and_non_executing(self):
        self.engine.create_improvement_proposal("experiment-proposal", {"recommendation": "Review queue"})
        self.engine.transition_improvement_proposal(
            "experiment-proposal", expected_state="proposed", new_state="approved",
            actor_id=self.admin_session.user.user_id,
        )
        path = "/v1/workforce/experiments"
        headers = [(b"content-type", b"application/json")]
        design = {
            "hypothesis": "A focused queue review reduces task failures",
            "intervention": "Introduce a human-reviewed queue triage checklist",
            "primary_metric": "failed_tasks",
            "preferred_direction": "decrease",
            "design_type": "pre_post_descriptive",
            "eligible_population": "Engineering workforce tasks",
            "guardrails": ["Do not change permissions", "No automatic dispatch changes"],
            "evaluation_window_days": 14,
            "minimum_sample_size": 10,
        }
        payload = {
            "experiment_id": "queue-triage-01",
            "proposal_id": "experiment-proposal",
            "design": design,
        }
        body = json.dumps(payload).encode()
        status, _ = self.request(path, method="POST", body=body, extra_headers=headers)
        self.assertEqual(status, 401)
        status, _ = self.request(path, method="POST", token=self.user_session.token, body=body, extra_headers=headers)
        self.assertEqual(status, 403)
        status, created = self.request(
            path, method="POST", token=self.admin_session.token, body=body, extra_headers=headers,
        )
        self.assertEqual(status, 201)
        self.assertEqual(created["state"], "proposed")
        self.assertEqual(created["design"], design)
        self.assertNotIn("execution", created)
        status, listing = self.request(path, token=self.admin_session.token)
        self.assertEqual(status, 200)
        self.assertEqual(listing["count"], 1)
        status, detail = self.request(path + "/queue-triage-01", token=self.admin_session.token)
        self.assertEqual(status, 200)
        self.assertEqual(detail["events"][0]["event_type"], "experiment.created")
        transition_path = path + "/queue-triage-01/transition"
        status, approved = self.request(
            transition_path, method="POST", token=self.admin_session.token,
            body=b'{"expected_state":"proposed","new_state":"approved","actor_id":"spoofed"}',
            extra_headers=headers,
        )
        self.assertEqual(status, 400)
        status, approved = self.request(
            transition_path, method="POST", token=self.admin_session.token,
            body=b'{"expected_state":"proposed","new_state":"approved","decision_note":"Design reviewed"}',
            extra_headers=headers,
        )
        self.assertEqual(status, 200)
        self.assertEqual(approved["state"], "approved")
        self.assertEqual(approved["decided_by"], self.admin_session.user.user_id)
        self.assertEqual(approved["events"][-1]["actor_id"], self.admin_session.user.user_id)
        self.assertNotIn("execution", approved)
        status, _ = self.request(
            transition_path, method="POST", token=self.admin_session.token,
            body=b'{"expected_state":"proposed","new_state":"rejected"}',
            extra_headers=headers,
        )
        self.assertEqual(status, 409)

    def test_experiment_api_requires_approved_proposal_and_exact_schema(self):
        self.engine.create_improvement_proposal("unapproved-experiment-proposal", {"recommendation": "Review"})
        payload = {
            "experiment_id": "blocked-experiment",
            "proposal_id": "unapproved-experiment-proposal",
            "design": {},
        }
        status, body = self.request(
            "/v1/workforce/experiments", method="POST", token=self.admin_session.token,
            body=json.dumps(payload).encode(), extra_headers=[(b"content-type", b"application/json")],
        )
        self.assertEqual(status, 409)
        self.assertEqual(body["error"], "experiment_conflict")
        status, body = self.request(
            "/v1/workforce/experiments", method="POST", token=self.admin_session.token,
            body=b'{"experiment_id":"x","proposal_id":"unapproved-experiment-proposal","design":{},"actor_id":"spoof"}',
            extra_headers=[(b"content-type", b"application/json")],
        )
        self.assertEqual(status, 400)

    def test_non_workforce_paths_are_delegated(self):
        status, body = self.request("/v1/conversations", token=self.admin_session.token)
        self.assertEqual(status, 404)
        self.assertEqual(body, "not found")


if __name__ == "__main__":
    unittest.main()
