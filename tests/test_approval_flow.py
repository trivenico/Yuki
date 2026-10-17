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
    manifest = SkillManifest.from_dict({
        "skill_id": "notes.write",
        "version": "1.0.0",
        "entrypoint": "worker.run",
        "input_schema": {"type": "object", "properties": {"text": {"type": "string", "maxLength": 100}},
                         "required": ["text"], "additionalProperties": False},
        "output_schema": {"type": "object", "properties": {"saved": {"type": "boolean"}},
                          "required": ["saved"], "additionalProperties": False},
        "capabilities": ["workspace.write"], "risk": "low",
        "timeout_seconds": 3, "max_output_bytes": 1024,
    })
    dispatcher = SkillDispatcher(capability_grants={"workspace.write"}, approval_store=ActionApprovalStore())
    script = "import json,sys; json.load(sys.stdin); print(json.dumps({'ok':True,'result':{'output':{'saved':True}}}))"
    dispatcher.register_subprocess(manifest, [sys.executable, "-c", script],
                                   executor=SubprocessExecutor(timeout_seconds=3))
    agent = YukiCoreAgent(dispatcher=dispatcher)
    planner = ManifestTaskAgent(Provider(json.dumps({
        "type": "tool_call", "skill_id": "notes.write", "arguments": {"text": "exact note"}
    })), agent, dispatcher)
    return planner


class ApprovalCoordinatorTests(unittest.TestCase):
    def test_approval_displays_exact_action_and_executes_stored_snapshot(self):
        planner = build()
        result = planner.run("Write note")
        coordinator = ApprovalCoordinator()
        pending = coordinator.record(planner, result, actor="owner", session_id="cli-session")
        shown = []
        approved = coordinator.approve(
            planner, pending.pending_id, actor="owner", session_id="cli-session",
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
            planner, pending.pending_id, actor="owner", session_id="cli-session",
            confirm=lambda action: False,
        )
        self.assertFalse(denied.ok)
        self.assertEqual(denied.status, "approval_denied")
        replay = coordinator.approve(
            planner, pending.pending_id, actor="owner", session_id="cli-session",
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

    def test_persistent_pending_and_audit_are_connected_to_approval_flow(self):
        with tempfile.TemporaryDirectory() as tmp:
            planner = build()
            pending_store = SQLitePendingApprovalStore(str(Path(tmp) / "pending.sqlite3"))
            audit = SQLiteApprovalAuditLog(str(Path(tmp) / "audit.sqlite3"))
            coordinator = ApprovalCoordinator(pending_store=pending_store, audit_log=audit)
            result = planner.run("Write note")
            pending = coordinator.record(planner, result, actor="owner", session_id="cli-session")
            approved = coordinator.approve(
                planner, pending.pending_id, actor="owner", session_id="cli-session",
                confirm=lambda action: action["arguments"] == {"text": "exact note"},
            )
            self.assertTrue(approved.ok)
            events = audit.list_events()
            self.assertEqual([event["event_type"] for event in reversed(events)],
                             ["requested", "approved", "executed"])
            self.assertNotIn("exact note", str(events))

    def test_persistent_approval_rejects_wrong_actor_and_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            planner = build()
            store = SQLitePendingApprovalStore(str(Path(tmp) / "pending.sqlite3"))
            audit = SQLiteApprovalAuditLog(str(Path(tmp) / "audit.sqlite3"))
            coordinator = ApprovalCoordinator(pending_store=store, audit_log=audit)
            pending = coordinator.record(
                planner, planner.run("Write note"), actor="owner", session_id="cli-session"
            )
            wrong_actor = coordinator.approve(
                planner, pending.pending_id, actor="other", session_id="cli-session",
                confirm=lambda action: True,
            )
            self.assertEqual(wrong_actor.status, "pending_not_found")
            wrong_session = coordinator.approve(
                planner, pending.pending_id, actor="owner", session_id="other-session",
                confirm=lambda action: True,
            )
            self.assertEqual(wrong_session.status, "pending_not_found")
            accepted = coordinator.approve(
                planner, pending.pending_id, actor="owner", session_id="cli-session",
                confirm=lambda action: True,
            )
            self.assertTrue(accepted.ok)

    def test_pending_capacity_is_bounded(self):
        planner = build()
        result = planner.run("Write note")
        coordinator = ApprovalCoordinator(max_pending=1)
        coordinator.record(planner, result, actor="owner", session_id="cli-session")
        with self.assertRaises(RuntimeError):
            coordinator.record(planner, result, actor="owner", session_id="cli-session")


if __name__ == "__main__":
    unittest.main()
