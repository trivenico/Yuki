import tempfile
import unittest
from pathlib import Path

from automation.workflow_approval import AuthenticatedPrincipal, WorkflowApprovalService
from automation.workflow_store import WorkflowRunStore, WorkflowConflict


class WorkflowApprovalServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = WorkflowRunStore(str(Path(self.tmp.name) / "workflow.sqlite3"))
        self.run = self.store.create(
            idempotency_key="approval-service",
            workflow_name="release",
            definition=[{"name": "publish", "action": "emit", "argument": "release", "requires_approval": True}],
        )
        self.contexts = {
            "valid-request": AuthenticatedPrincipal(
                actor_id="user-42", session_id="server-session-abc",
                permissions=frozenset({"workflow:approve"}),
            ),
            "read-only-request": AuthenticatedPrincipal(
                actor_id="user-42", session_id="server-session-abc",
                permissions=frozenset({"workflow:read"}),
            ),
        }
        self.service = WorkflowApprovalService(
            self.store, principal_resolver=lambda ctx: self.contexts[ctx],
        )

    def test_approval_uses_principal_from_trusted_resolver(self):
        approved = self.service.approve(
            self.run.run_id, request_context="valid-request",
            expected_version=self.run.version,
        )
        self.assertEqual(approved.approved_by, "user-42")
        self.assertIsNotNone(approved.approved_session_hash)
        claimed = self.store.claim_next(worker_id="worker-a")
        self.assertEqual(claimed.run_id, self.run.run_id)

    def test_missing_permission_is_denied_before_persistence(self):
        with self.assertRaises(PermissionError):
            self.service.approve(
                self.run.run_id, request_context="read-only-request",
                expected_version=self.run.version,
            )
        self.assertIsNone(self.store.get(self.run.run_id).approved_by)
        self.assertIsNone(self.store.claim_next(worker_id="worker-a"))

    def test_resource_authorization_failure_is_closed(self):
        service = WorkflowApprovalService(
            self.store,
            principal_resolver=lambda ctx: self.contexts[ctx],
            authorize=lambda principal, run: False,
        )
        with self.assertRaises(PermissionError):
            service.approve(
                self.run.run_id, request_context="valid-request",
                expected_version=self.run.version,
            )
        self.assertIsNone(self.store.get(self.run.run_id).approved_by)

    def test_authorizer_exception_is_denied(self):
        def broken_authorizer(principal, run):
            raise RuntimeError("internal detail must not leak")
        service = WorkflowApprovalService(
            self.store,
            principal_resolver=lambda ctx: self.contexts[ctx],
            authorize=broken_authorizer,
        )
        with self.assertRaises(PermissionError):
            service.approve(
                self.run.run_id, request_context="valid-request",
                expected_version=self.run.version,
            )
        self.assertIsNone(self.store.get(self.run.run_id).approved_by)

    def test_stale_version_does_not_approve(self):
        with self.assertRaises(WorkflowConflict):
            self.service.approve(
                self.run.run_id, request_context="valid-request",
                expected_version=self.run.version + 1,
            )
        self.assertIsNone(self.store.get(self.run.run_id).approved_by)

    def test_untrusted_resolver_result_is_rejected(self):
        service = WorkflowApprovalService(self.store, principal_resolver=lambda ctx: {
            "actor_id": "forged", "session_id": "forged",
            "permissions": ["workflow:approve"],
        })
        with self.assertRaises(PermissionError):
            service.approve(
                self.run.run_id, request_context="forged-request",
                expected_version=self.run.version,
            )
        self.assertIsNone(self.store.get(self.run.run_id).approved_by)


if __name__ == "__main__":
    unittest.main()
