import asyncio
import json
import sqlite3
from contextlib import closing
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from api.sandbox_execution import AuthenticatedSandboxExecutionAPI
from automation.execution_task import DurableSandboxExecutionWorker
from automation.sandbox_execution_approval import (
    SQLiteSandboxExecutionApprovalStore, SandboxExecutionAuditWriter,
)
from automation.sandbox_execution_submission import SandboxExecutionSubmissionService
from core.approval_audit import SQLiteApprovalAuditLog
from core.auth import BuiltInAuthService
from core.durable_task_engine import DurableTaskEngine
from hackback.sandbox import DockerSandbox


class _Downstream:
    async def __call__(self, scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b'{"delegated":true}'})


class AuthenticatedSandboxExecutionAPITests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.auth = BuiltInAuthService(str(root / "auth.sqlite3"), password_iterations=100_000)
        self.auth.bootstrap_admin("owner-one", "A secure password 1!")
        self.owner_one = self.auth.authenticate("owner-one", "A secure password 1!")
        self.auth.create_account("owner-two", "Another secure password 2!")
        with closing(sqlite3.connect(self.auth.database_path)) as db:
            db.execute("UPDATE auth_users SET role='admin' WHERE username='owner-two'")
        self.owner_two = self.auth.authenticate("owner-two", "Another secure password 2!")
        self.image = "registry.example/yuki@sha256:" + "a" * 64
        self.workspace = root / "sandbox-workspace"
        self.workspace.mkdir()
        self.engine = DurableTaskEngine(str(root / "tasks.sqlite3"))
        self.addCleanup(self.engine.close)
        self.store = SQLiteSandboxExecutionApprovalStore(
            str(root / "auth.sqlite3"), auth_service=self.auth,
        )
        self.audit = SQLiteApprovalAuditLog(str(root / "audit.sqlite3"))
        self.sandbox = DockerSandbox(
            self.workspace, allowed_images=(self.image,), enabled=True,
            approver=lambda _reason: True,
        )
        self.worker = DurableSandboxExecutionWorker.from_trusted_stores(
            self.engine, worker_id="api-test-worker", sandbox=self.sandbox,
            approval_store=self.store, audit_writer=SandboxExecutionAuditWriter(self.audit),
        )
        self.service = SandboxExecutionSubmissionService(
            auth_service=self.auth, engine=self.engine, worker=self.worker,
            approval_store=self.store,
        )
        self.app = AuthenticatedSandboxExecutionAPI(
            _Downstream(), self.auth, self.service,
            workspace=str(self.workspace), allowed_images=(self.image,),
        )

    def request(self, method, path, payload=None, token=None, extra_headers=()):
        async def run():
            body = json.dumps(payload).encode() if payload is not None else b""
            consumed = False
            sent = []
            async def receive():
                nonlocal consumed
                if consumed:
                    return {"type": "http.disconnect"}
                consumed = True
                return {"type": "http.request", "body": body, "more_body": False}
            async def send(message):
                sent.append(message)
            headers = list(extra_headers)
            if token:
                headers.append((b"authorization", f"Bearer {token}".encode()))
            await self.app({
                "type": "http", "method": method, "path": path,
                "headers": headers, "query_string": b"",
            }, receive, send)
            return sent[0]["status"], json.loads(sent[1]["body"])
        return asyncio.run(run())


    def test_operator_reconciliation_endpoints_require_admin_and_return_safe_metadata(self):
        status, _ = self.request(
            "GET", "/v1/sandbox/operator/unknown-outcomes", token=self.owner_one.token,
        )
        self.assertEqual(status, 200)
        status, _ = self.request(
            "GET", "/v1/sandbox/operator/workers", token=self.owner_one.token,
        )
        self.assertEqual(status, 200)

        self.auth.create_account("operator-user", "Operator secure password 3!")
        with closing(sqlite3.connect(self.auth.database_path)) as db:
            db.execute("UPDATE auth_users SET role='operator' WHERE username='operator-user'")
        operator = self.auth.authenticate("operator-user", "Operator secure password 3!")
        status, body = self.request(
            "GET", "/v1/sandbox/operator/unknown-outcomes", token=operator.token,
        )
        self.assertEqual((status, body["error"]), (403, "forbidden"))

        goal = self.engine.create_owned_goal(self.owner_one.user.user_id)
        task = self.engine.submit(
            goal, "sandbox.execution.v1", {"secret": "never expose"},
            idempotency_key="api-unknown", max_attempts=1,
        )
        self.engine.claim("api-reconcile-worker", task_types=("sandbox.execution.v1",))
        self.engine.fail_unknown_outcome(task["id"], "api-reconcile-worker")
        status, body = self.request(
            "GET", "/v1/sandbox/operator/unknown-outcomes", token=self.owner_one.token,
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["unknown_outcomes"][0]["id"], task["id"])
        self.assertNotIn("payload", body["unknown_outcomes"][0])
        self.assertNotIn("secret", json.dumps(body))
        self.engine.register_worker("api-worker", 4321)
        status, body = self.request(
            "GET", "/v1/sandbox/operator/workers", token=self.owner_one.token,
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["workers"][0]["worker_id"], "api-worker")


    def test_authenticated_api_to_durable_worker_execution_lifecycle(self):
        status, goal = self.request(
            "POST", "/v1/sandbox/goals", {}, self.owner_one.token,
        )
        self.assertEqual(status, 201)
        task_payload = {
            "task_id": "api-e2e-task-01", "image": self.image,
            "argv": ["python", "--version"], "timeout_seconds": 10,
            "write_workspace": False, "max_output_bytes": 2048,
        }
        status, approval = self.request(
            "POST", "/v1/sandbox/approvals", task_payload, self.owner_one.token,
        )
        self.assertEqual(status, 201)
        status, queued = self.request(
            "POST", f"/v1/sandbox/goals/{goal['goal_id']}/tasks",
            {"task": task_payload, "approval_id": approval["approval_id"]},
            self.owner_one.token,
            extra_headers=((b"idempotency-key", b"api-e2e-submit-01"),),
        )
        self.assertEqual(status, 202)
        task_id = queued["task"]["task_id"]
        with unittest.mock.patch.object(
            self.sandbox, "run",
            return_value={"returncode": 0, "stdout": "python 3", "stderr": ""},
        ) as sandbox_run:
            completed = self.worker.run_once()
        sandbox_run.assert_called_once()
        self.assertEqual(completed["id"], task_id)
        self.assertEqual(completed["state"], "succeeded")
        status, body = self.request(
            "GET", f"/v1/sandbox/tasks/{task_id}", token=self.owner_one.token,
        )
        self.assertEqual(status, 200)
        self.assertEqual(body["task"]["state"], "succeeded")

    def test_requires_auth_and_delegates_non_sandbox_routes(self):
        status, body = self.request("POST", "/v1/sandbox/goals", {})
        self.assertEqual((status, body["error"]), (401, "authentication_required"))
        status, body = self.request("GET", "/v1/conversations", token=self.owner_one.token)
        self.assertEqual((status, body["delegated"]), (200, True))

    def test_approval_submission_and_owner_scoped_status(self):
        status, goal = self.request(
            "POST", "/v1/sandbox/goals", {}, self.owner_one.token,
        )
        self.assertEqual(status, 201)
        task_payload = {
            "task_id": "api-task-0001", "image": self.image,
            "argv": ["python", "--version"], "timeout_seconds": 30,
            "write_workspace": False, "max_output_bytes": 2048,
        }
        status, approval = self.request(
            "POST", "/v1/sandbox/approvals", task_payload, self.owner_one.token,
        )
        self.assertEqual(status, 201)
        self.assertEqual(approval["task_id"], task_payload["task_id"])
        headers = ((b"idempotency-key", b"api-request-0001"),)
        submit_body = {"task": task_payload, "approval_id": approval["approval_id"]}
        status, first = self.request(
            "POST", f"/v1/sandbox/goals/{goal['goal_id']}/tasks",
            submit_body, self.owner_one.token, headers,
        )
        self.assertEqual(status, 202)
        status, second = self.request(
            "POST", f"/v1/sandbox/goals/{goal['goal_id']}/tasks",
            submit_body, self.owner_one.token, headers,
        )
        self.assertEqual(status, 202)
        self.assertEqual(first["task"]["task_id"], second["task"]["task_id"])
        status, own = self.request(
            "GET", f"/v1/sandbox/tasks/{first['task']['task_id']}",
            token=self.owner_one.token,
        )
        self.assertEqual((status, own["task"]["state"]), (200, "queued"))
        status, denied = self.request(
            "GET", f"/v1/sandbox/tasks/{first['task']['task_id']}",
            token=self.owner_two.token,
        )
        self.assertEqual((status, denied["error"]), (403, "forbidden"))

    def test_rejects_unallowlisted_images_and_missing_idempotency_key(self):
        payload = {"task_id": "api-task-0002", "image": "untrusted:latest", "argv": ["echo", "no"]}
        status, body = self.request(
            "POST", "/v1/sandbox/approvals", payload, self.owner_one.token,
        )
        self.assertEqual((status, body["error"]), (400, "invalid_request"))
        payload["image"] = self.image
        status, body = self.request(
            "POST", "/v1/sandbox/approvals", payload, self.owner_one.token,
        )
        self.assertEqual(status, 201)
        goal_status, goal = self.request(
            "POST", "/v1/sandbox/goals", {}, self.owner_one.token,
        )
        self.assertEqual(goal_status, 201)
        status, body = self.request(
            "POST", f"/v1/sandbox/goals/{goal['goal_id']}/tasks",
            {"task": payload, "approval_id": body["approval_id"]}, self.owner_one.token,
        )
        self.assertEqual((status, body["error"]), (400, "idempotency_key_required"))

    def test_revoked_session_cannot_access_sandbox_api(self):
        self.auth.revoke_session(self.owner_one.token)
        status, body = self.request("POST", "/v1/sandbox/goals", {}, self.owner_one.token)
        self.assertEqual((status, body["error"]), (401, "authentication_required"))
