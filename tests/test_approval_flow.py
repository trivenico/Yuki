import json
import sys
import tempfile
import unittest
from pathlib import Path

from core.action_approvals import ActionApprovalStore
from core.agent import YukiCoreAgent
from core.approval_flow import ApprovalCoordinator
from core.approval_audit import SQLiteApprovalAuditLog
from core.pending_approvals import SQLitePendingApprovalStore
from core.manifest_task_agent import ManifestTaskAgent
from core.skill_dispatch import SkillDispatcher
from core.skill_manifest import SkillManifest
from core.subprocess_executor import SubprocessExecutor


class Provider:
    def __init__(self, response):
        self.response = response

    def generate(self, prompt):
        return self.response


def build():
    manifest = SkillManifest.from_dict(
        {
            "skill_id": "notes.write",
            "version": "1.0.0",
            "entrypoint": "worker.run",
            "input_schema": {
                "type": "object",
                "properties": {"text": {"type": "string", "maxLength": 100}},
                "required": ["text"],
                "additionalProperties": False,
            },
            "output_schema": {
                "type": "object",
                "properties": {"saved": {"type": "boolean"}},
                "required": ["saved"],
                "additionalProperties": False,
            },
            "capabilities": ["workspace.write"],
            "risk": "low",
            "timeout_seconds": 3,
            "max_output_bytes": 1024,
        }
    )
    dispatcher = SkillDispatcher(
        capability_grants={"workspace.write"}, approval_store=ActionApprovalStore()
    )
    script = "import json,sys; json.load(sys.stdin); print(json.dumps({'ok':True,'result':{'output':{'saved':True}}}))"
    dispatcher.register_subprocess(
        manifest, [sys.executable, "-c", script], executor=SubprocessExecutor(timeout_seconds=3)
    )
    agent = YukiCoreAgent(dispatcher=dispatcher)
    planner = ManifestTaskAgent(
        Provider(
            json.dumps(
                {
                    "type": "tool_call",
                    "skill_id": "notes.write",
                    "arguments": {"text": "exact note"},
                }
            )
        ),
        agent,
        dispatcher,
    )
    return planner


class ApprovalCoordinatorTests(unittest.TestCase):
    def test_approval_displays_exact_action_and_executes_stored_snapshot(self):
        planner = build()
        result = planner.run("Write note")
        coordinator = ApprovalCoordinator()
        pending = coordinator.record(planner, result, actor="owner", session_id="cli-session")
        shown = []
        approved = coordinator.approve(
            planner,
            pending.pending_id,
            actor="owner",
            session_id="cli-session",
            confirm=lambda action: shown.append(action) or True,
        )
        self.assertTrue(approved.ok)
        self.assertEqual(approved.status, "completed")
        self.assertEqual(shown[0]["skill_id"], "notes.write")
        self.assertEqual(shown[0]["arguments"], {"text": "exact note"})

    def test_denial_does_not_execute(self):
        planner = build()
        result = planner.run("Write note")
        coordinator = ApprovalCoordinator()
        pending = coordinator.record(planner, result, actor="owner", session_id="cli-session")
        denied = coordinator.approve(
            planner,
            pending.pending_id,
            actor="owner",
            session_id="cli-session",
            confirm=lambda action: False,
        )
        self.assertFalse(denied.ok)
        self.assertEqual(denied.status, "approval_denied")
        replay = coordinator.approve(
            planner,
            pending.pending_id,
            actor="owner",
            session_id="cli-session",
            confirm=lambda action: True,
        )
        self.assertEqual(replay.status, "pending_not_found")

    def test_callback_cannot_mutate_stored_action(self):
        planner = build()
        result = planner.run("Write note")
        coordinator = ApprovalCoordinator()
        pending = coordinator.record(planner, result, actor="owner", session_id="cli-session")

        def mutate(action):
            action["arguments"]["text"] = "changed by UI"
            return True

        approved = coordinator.approve(
            planner, pending.pending_id, actor="owner", session_id="cli-session", confirm=mutate
        )
        self.assertTrue(approved.ok)

    def test_approval_lifecycle_logs_metadata_without_arguments(self):
        planner = build()
        result = planner.run("Write note")
        coordinator = ApprovalCoordinator()
        with self.assertLogs("yuki.approval", level="INFO") as captured:
            pending = coordinator.record(
                planner, result, actor="owner-secret", session_id="session-secret"
            )
            approved = coordinator.approve(
                planner,
                pending.pending_id,
                actor="owner-secret",
                session_id="session-secret",
                confirm=lambda action: action["arguments"] == {"text": "exact note"},
            )
        self.assertTrue(approved.ok)
        rendered = "\\n".join(captured.output)
        self.assertIn("approval.requested", rendered)
        self.assertIn("approval.approved", rendered)
        self.assertIn("approval.executed", rendered)
        self.assertNotIn("exact note", rendered)
        self.assertNotIn("owner-secret", rendered)
        self.assertNotIn("session-secret", rendered)

    def test_post_execution_audit_failure_reports_completed_with_warning(self):
        class FailingExecutedAudit:
            def record(self, event, **kwargs):
                if event == "executed":
                    raise OSError("audit disk unavailable")

        planner = build()
        result = planner.run("Write note")
        coordinator = ApprovalCoordinator(audit_log=FailingExecutedAudit())
        with self.assertLogs("yuki.approval", level="ERROR") as captured:
            pending = coordinator.record(planner, result, actor="owner", session_id="cli-session")
            approved = coordinator.approve(
                planner,
                pending.pending_id,
                actor="owner",
                session_id="cli-session",
                confirm=lambda action: True,
            )
        self.assertTrue(approved.ok)
        self.assertEqual(approved.status, "completed_audit_warning")
        self.assertIn("approval.execution_audit_persistence_failed", "\\n".join(captured.output))

    def test_request_audit_failure_does_not_leave_approvable_action(self):
        class FailingRequestedAudit:
            def record(self, event, **kwargs):
                if event == "requested":
                    raise OSError("audit disk unavailable")

        planner = build()
        coordinator = ApprovalCoordinator(audit_log=FailingRequestedAudit())
        with self.assertRaisesRegex(RuntimeError, "no approval was issued"):
            coordinator.record(
                planner, planner.run("Write note"), actor="owner", session_id="cli-session"
            )
        self.assertEqual(coordinator._pending, {})

    def test_approval_audit_failure_before_execution_fails_closed(self):
        class FailingApprovedAudit:
            def record(self, event, **kwargs):
                if event == "approved":
                    raise OSError("audit disk unavailable")

        planner = build()
        result = planner.run("Write note")
        coordinator = ApprovalCoordinator(audit_log=FailingApprovedAudit())
        pending = coordinator.record(planner, result, actor="owner", session_id="cli-session")
        executed = []
        original = planner.dispatcher.execute_approved

        def track_execution(action, **kwargs):
            executed.append(True)
            return original(action, **kwargs)

        planner.dispatcher.execute_approved = track_execution
        approved = coordinator.approve(
            planner,
            pending.pending_id,
            actor="owner",
            session_id="cli-session",
            confirm=lambda action: True,
        )
        self.assertFalse(approved.ok)
        self.assertEqual(approved.status, "approval_failed")
        self.assertEqual(executed, [])

    def test_persistent_pending_and_audit_are_connected_to_approval_flow(self):
        with tempfile.TemporaryDirectory() as tmp:
            planner = build()
            pending_store = SQLitePendingApprovalStore(str(Path(tmp) / "pending.sqlite3"))
            audit = SQLiteApprovalAuditLog(str(Path(tmp) / "audit.sqlite3"))
            coordinator = ApprovalCoordinator(pending_store=pending_store, audit_log=audit)
            result = planner.run("Write note")
            pending = coordinator.record(planner, result, actor="owner", session_id="cli-session")
            approved = coordinator.approve(
                planner,
                pending.pending_id,
                actor="owner",
                session_id="cli-session",
                confirm=lambda action: action["arguments"] == {"text": "exact note"},
            )
            self.assertTrue(approved.ok)
            events = audit.list_events()
            self.assertEqual(
                [event["event_type"] for event in reversed(events)],
                ["requested", "approved", "executed"],
            )
            self.assertNotIn("exact note", str(events))

    def test_pending_approval_is_bound_to_creator_actor_and_session(self):
        planner = build()
        result = planner.run("Write note")
        coordinator = ApprovalCoordinator()
        pending = coordinator.record(planner, result, actor="owner", session_id="cli-session")
        wrong_actor = coordinator.approve(
            planner,
            pending.pending_id,
            actor="other-user",
            session_id="cli-session",
            confirm=lambda action: True,
        )
        self.assertEqual(wrong_actor.status, "pending_not_found")
        wrong_session = coordinator.approve(
            planner,
            pending.pending_id,
            actor="owner",
            session_id="other-session",
            confirm=lambda action: True,
        )
        self.assertEqual(wrong_session.status, "pending_not_found")
        valid = coordinator.approve(
            planner,
            pending.pending_id,
            actor="owner",
            session_id="cli-session",
            confirm=lambda action: True,
        )
        self.assertTrue(valid.ok)

    def test_persistent_pending_approval_rejects_other_actor_and_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            planner = build()
            pending_store = SQLitePendingApprovalStore(str(Path(tmp) / "pending.sqlite3"))
            coordinator = ApprovalCoordinator(pending_store=pending_store)
            pending = coordinator.record(
                planner, planner.run("Write note"), actor="owner", session_id="cli-session"
            )
            wrong = coordinator.approve(
                planner,
                pending.pending_id,
                actor="other-user",
                session_id="cli-session",
                confirm=lambda action: True,
            )
            self.assertEqual(wrong.status, "pending_not_found")
            wrong_session = coordinator.approve(
                planner,
                pending.pending_id,
                actor="owner",
                session_id="other-session",
                confirm=lambda action: True,
            )
            self.assertEqual(wrong_session.status, "pending_not_found")
            valid = coordinator.approve(
                planner,
                pending.pending_id,
                actor="owner",
                session_id="cli-session",
                confirm=lambda action: True,
            )
            self.assertTrue(valid.ok)

    def test_pending_capacity_is_bounded(self):
        planner = build()
        result = planner.run("Write note")
        coordinator = ApprovalCoordinator(max_pending=1)
        coordinator.record(planner, result, actor="owner", session_id="cli-session")
        with self.assertRaises(RuntimeError):
            coordinator.record(planner, result, actor="owner", session_id="cli-session")


    def test_dispatcher_rejects_direct_execution_without_approval_receipt(self):
        planner = build()
        proposal = planner.run("Write note")
        result = planner.dispatcher.execute_approved(proposal.action)
        self.assertFalse(result.ok)
        self.assertEqual(result.status, "approval_required")

    def test_approval_receipt_is_single_use(self):
        planner = build()
        proposal = planner.run("Write note")
        dispatcher = planner.dispatcher
        receipt = dispatcher._issue_approval_receipt()
        first = dispatcher.execute_approved(proposal.action, approval_receipt=receipt)
        second = dispatcher.execute_approved(proposal.action, approval_receipt=receipt)
        self.assertTrue(first.ok)
        self.assertFalse(second.ok)
        self.assertEqual(second.status, "approval_required")

if __name__ == "__main__":
    unittest.main()
