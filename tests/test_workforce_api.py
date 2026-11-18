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

    def test_non_workforce_paths_are_delegated(self):
        status, body = self.request("/v1/conversations", token=self.admin_session.token)
        self.assertEqual(status, 404)
        self.assertEqual(body, "not found")


if __name__ == "__main__":
    unittest.main()
