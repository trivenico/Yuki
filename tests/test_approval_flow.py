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

    def test_pending_approval_is_bound_to_creator_actor_and_session(self):
        planner = build()
        result = planner.run("Write note")
        coordinator = ApprovalCoordinator()
        pending = coordinator.record(planner, result, actor="owner", session_id="cli-session")
        wrong_actor = coordinator.approve(
            planner, pending.pending_id, actor="other-user", session_id="cli-session",
            confirm=lambda action: True,
        )
        self.assertEqual(wrong_actor.status, "pending_not_found")
        wrong_session = coordinator.approve(
            planner, pending.pending_id, actor="owner", session_id="other-session",
            confirm=lambda action: True,
        )
        self.assertEqual(wrong_session.status, "pending_not_found")
        valid = coordinator.approve(
            planner, pending.pending_id, actor="owner", session_id="cli-session",
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
                planner, pending.pending_id, actor="other-user", session_id="cli-session",
                confirm=lambda action: True,
            )
            self.assertEqual(wrong.status, "pending_not_found")
            wrong_session = coordinator.approve(
                planner, pending.pending_id, actor="owner", session_id="other-session",
                confirm=lambda action: True,
            )
            self.assertEqual(wrong_session.status, "pending_not_found")
            valid = coordinator.approve(
                planner, pending.pending_id, actor="owner", session_id="cli-session",
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


class ManifestSchemaHardeningTests(unittest.TestCase):
    def manifest(self, input_schema=None, output_schema=None):
        return {
            "skill_id": "safe.echo",
            "version": "1.0.0",
            "entrypoint": "worker.run",
            "input_schema": input_schema or {
                "type": "object",
                "properties": {"text": {"type": "string", "minLength": 1, "maxLength": 8}},
                "required": ["text"],
                "additionalProperties": False,
            },
            "output_schema": output_schema or {
                "type": "object",
                "properties": {"ok": {"type": "boolean"}},
                "required": ["ok"],
                "additionalProperties": False,
            },
            "capabilities": [],
            "risk": "low",
            "timeout_seconds": 3,
            "max_output_bytes": 1024,
        }

    def test_nested_schema_constraints_are_enforced(self):
        schema = {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {"count": {"type": "integer", "minimum": 1, "maximum": 3}},
                        "required": ["count"],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["items"],
            "additionalProperties": False,
        }
        manifest = SkillManifest.from_dict(self.manifest(input_schema=schema))
        self.assertEqual(manifest.validate_object({"items": [{"count": 2}]}, manifest.input_schema),
                         {"items": [{"count": 2}]})
        with self.assertRaises(ValueError):
            manifest.validate_object({"items": [{"count": 0}]}, manifest.input_schema)
        with self.assertRaises(ValueError):
            manifest.validate_object({"items": [{"count": True}]}, manifest.input_schema)

    def test_unknown_schema_keywords_fail_closed(self):
        bad = {
            "type": "object",
            "properties": {"text": {"type": "string", "pattern": ".*"}},
            "required": ["text"],
            "additionalProperties": False,
        }
        with self.assertRaisesRegex(ValueError, "unsupported schema keyword"):
            SkillManifest.from_dict(self.manifest(input_schema=bad))

    def test_required_keys_must_be_declared(self):
        bad = {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["missing"],
            "additionalProperties": False,
        }
        with self.assertRaisesRegex(ValueError, "required must name"):
            SkillManifest.from_dict(self.manifest(input_schema=bad))

    def test_string_bounds_and_enum_are_enforced(self):
        schema = {
            "type": "object",
            "properties": {"mode": {"type": "string", "enum": ["read", "write"]}},
            "required": ["mode"],
            "additionalProperties": False,
        }
        manifest = SkillManifest.from_dict(self.manifest(input_schema=schema))
        with self.assertRaises(ValueError):
            manifest.validate_object({"mode": "delete"}, manifest.input_schema)

    def test_schema_depth_is_bounded(self):
        nested = {"type": "string"}
        for _ in range(18):
            nested = {"type": "array", "items": nested}
        bad = self.manifest(input_schema={
            "type": "object", "properties": {"items": nested},
            "required": ["items"], "additionalProperties": False,
        })
        with self.assertRaisesRegex(ValueError, "invalid schema"):
            SkillManifest.from_dict(bad)


if __name__ == "__main__":
    unittest.main()
