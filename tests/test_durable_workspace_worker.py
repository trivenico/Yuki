import hashlib
import tempfile
import unittest
from pathlib import Path

from automation.approved_workspace_changes import ApprovedWorkspaceChanges
from automation.authorized_read_tools import ToolPrincipal
from core.capability_approval_preview import ApprovalPreview, CapabilityApprovalPreviewRegistry
from core.durable_task_engine import DurableTaskEngine
from core.durable_workspace_worker import DurableWorkspaceWorker
from core.capability_approval_gateway import CapabilityApprovalGateway
from core.tool_policy import Decision, PolicyRequest, PolicyRule, Risk, ToolPolicyEngine


class DurableWorkspaceWorkerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.db = self.root / "tasks.sqlite"
        self.engine = DurableTaskEngine(self.db)
        self.addCleanup(self.engine.close)
        self.owner = "owner:test"
        self.goal = self.engine.create_owned_goal(self.owner, "goal-workspace")
        self.workspace_id = "workspace:project-a"
        self.target = self.root / "README.md"
        self.target.write_bytes(b"before\n")
        self.changes = [{
            "path": "README.md",
            "expected_sha256": hashlib.sha256(b"before\n").hexdigest(),
            "content": "after\n",
        }]
        self.policy = ToolPolicyEngine((
            PolicyRule(
                "owner-workspace-write", Decision.REQUIRE_APPROVAL,
                capability="workspace:write", principal_id=self.owner,
                target=self.workspace_id, risk=Risk.HIGH,
            ),
        ), policy_version="test-policy-v1")
        previews = CapabilityApprovalPreviewRegistry()
        previews.register(
            "workspace:write",
            lambda request: ApprovalPreview(
                title="Workspace edit", operation="Update approved files",
                target="Project A", impact="The exact proposed file contents will be written",
            ),
            operation_type="workspace.write",
        )
        def seal(plaintext, aad):
            return b"test-sealed:" + hashlib.sha256(aad).digest() + plaintext
        def unseal(ciphertext, aad):
            prefix = b"test-sealed:" + hashlib.sha256(aad).digest()
            if not ciphertext.startswith(prefix):
                raise ValueError("invalid test envelope")
            return ciphertext[len(prefix):]
        self.gateway = CapabilityApprovalGateway(
            self.root / "approvals.sqlite", self.policy,
            authorize_approver=lambda actor, request: actor == "owner-admin",
            preview_registry=previews,
            seal_operation_payload=seal,
            unseal_operation_payload=unseal,
        )
        self.audit = []
        self.executor = ApprovedWorkspaceChanges(
            self.root,
            trusted_principal=lambda principal: principal.actor_id == self.owner,
            approve_change=lambda actor, change_id, digest: True,
            audit=self.audit.append,
            allowed_paths=("README.md",),
        )
        self.request_ids = []
        self.delivered_tokens = []

    def request_factory(self, context, workspace_id, changes, operation):
        return PolicyRequest(
            principal_id=context.principal_id,
            trigger=context.trigger,
            capability=operation.capability,
            target=operation.target,
            argument_sha256=operation.payload_sha256,
            risk=Risk.HIGH,
            run_id=context.goal_id,
            step_id=context.task_id,
            claim_token_sha256=context.claim_token_sha256,
        )

    def principal_factory(self, context):
        return ToolPrincipal(context.principal_id, frozenset({"workspace:write"}))

    def approve_from_test_resolver(self, context, task_type, lease_token):
        pending = self.gateway.pending_requests()
        if not pending:
            return None
        request_id = pending[0]["request_id"]
        self.request_ids.append(request_id)
        token = self.gateway.approve_request(
            request_id, approved_by="owner-admin",
        )
        self.delivered_tokens.append(token)
        return token

    def submit(self, *, workspace_id=None, changes=None):
        return self.engine.submit(
            self.goal, "workspace.apply",
            {"workspace_id": workspace_id or self.workspace_id,
             "changes": self.changes if changes is None else changes},
            idempotency_key="workspace-edit", max_attempts=1, owner_id=self.owner,
        )

    def worker(self, resolver=None):
        return DurableWorkspaceWorker(
            self.engine, gateway=self.gateway, executor=self.executor,
            worker_id="workspace-worker",
            policy_request_factory=self.request_factory,
            principal_factory=self.principal_factory,
            approval_token_resolver=resolver or self.approve_from_test_resolver,
            lease_seconds=30, heartbeat_interval_seconds=10,
            approval_wait_timeout_seconds=5,
        )

    def test_policy_trigger_must_match_trusted_durable_context(self):
        def mismatched_trigger(context, workspace_id, changes, operation):
            request = self.request_factory(context, workspace_id, changes, operation)
            return PolicyRequest(
                principal_id=request.principal_id,
                trigger="interactive",
                capability=request.capability,
                target=request.target,
                argument_sha256=request.argument_sha256,
                risk=request.risk,
                run_id=request.run_id,
                step_id=request.step_id,
                claim_token_sha256=request.claim_token_sha256,
            )

        self.submit()
        result = DurableWorkspaceWorker(
            self.engine, gateway=self.gateway, executor=self.executor,
            worker_id="workspace-worker",
            policy_request_factory=mismatched_trigger,
            principal_factory=self.principal_factory,
            approval_token_resolver=self.approve_from_test_resolver,
            lease_seconds=30, heartbeat_interval_seconds=10,
            approval_wait_timeout_seconds=5,
        ).run_once()
        self.assertEqual(result["state"], "failed")
        self.assertEqual(self.target.read_text(encoding="utf-8"), "before\n")
        self.assertEqual(self.request_ids, [])
        self.assertEqual(self.gateway.pending_requests(), ())

    def test_principal_factory_cannot_substitute_workspace_owner(self):
        self.submit()
        worker = DurableWorkspaceWorker(
            self.engine, gateway=self.gateway, executor=self.executor,
            worker_id="workspace-worker",
            policy_request_factory=self.request_factory,
            principal_factory=lambda context: ToolPrincipal(
                "owner:attacker", frozenset({"workspace:write"}),
            ),
            approval_token_resolver=self.approve_from_test_resolver,
            lease_seconds=30, heartbeat_interval_seconds=10,
            approval_wait_timeout_seconds=5,
        )
        result = worker.run_once()
        self.assertEqual(result["state"], "failed")
        self.assertEqual(self.target.read_text(encoding="utf-8"), "before\n")
        self.assertEqual(self.request_ids, [])
        self.assertEqual(self.delivered_tokens, [])

    def test_owner_approved_workspace_task_executes_exact_change(self):
        task = self.submit()
        result = self.worker().run_once()
        self.assertEqual(result["task_id"], task["id"])
        self.assertEqual(result["state"], "succeeded")
        self.assertEqual(result["status"], "workspace_changes_applied")
        self.assertEqual(self.target.read_text(encoding="utf-8"), "after\n")
        self.assertEqual(len(self.request_ids), 1)
        self.assertEqual(self.gateway.get_request_status(self.request_ids[0]), "approved")
        waits = self.engine.list_approval_waits(task["id"])
        self.assertEqual(len(waits), 1)
        self.assertEqual(waits[0]["state"], "completed")

    def test_rejected_workspace_approval_has_no_file_side_effect(self):
        self.submit()
        def reject(context, task_type, lease_token):
            pending = self.gateway.pending_requests()
            self.gateway.reject_request(
                pending[0]["request_id"], rejected_by="owner-admin",
            )
            return None
        result = self.worker(reject).run_once()
        self.assertEqual(result["state"], "failed")
        self.assertEqual(self.target.read_text(encoding="utf-8"), "before\n")

    def test_invalid_workspace_path_fails_without_side_effect(self):
        self.submit(changes=[{
            "path": "../outside.txt",
            "expected_sha256": "0" * 64,
            "content": "malicious",
        }])
        result = self.worker().run_once()
        self.assertEqual(result["state"], "failed")
        self.assertEqual(self.target.read_text(encoding="utf-8"), "before\n")
        self.assertEqual(self.request_ids, [])

    def test_stale_workspace_digest_fails_before_creating_approval(self):
        self.submit()
        self.target.write_bytes(b"changed by another process\\n")
        result = self.worker().run_once()
        self.assertEqual(result["state"], "failed")
        self.assertEqual(self.target.read_text(encoding="utf-8"), "changed by another process\\n")
        self.assertEqual(self.request_ids, [])
        self.assertEqual(self.delivered_tokens, [])
        self.assertEqual(self.gateway.pending_requests(), ())

    def test_unowned_goal_is_not_dispatched(self):
        unowned_goal = self.engine.create_goal("legacy-unowned")
        self.engine.submit(
            unowned_goal, "workspace.apply",
            {"workspace_id": self.workspace_id, "changes": self.changes},
            idempotency_key="unowned", max_attempts=1,
        )
        result = self.worker().run_once()
        self.assertEqual(result["status"], "invalid_claim_identity")
        self.assertEqual(self.target.read_text(encoding="utf-8"), "before\n")
        self.assertEqual(self.request_ids, [])


if __name__ == "__main__":
    unittest.main()
