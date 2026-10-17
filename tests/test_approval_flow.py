import json
import sys
import unittest

from core.action_approvals import ActionApprovalStore
from core.agent import YukiCoreAgent
from core.approval_flow import ApprovalCoordinator
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
        pending = coordinator.record(planner, result)
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
        pending = coordinator.record(planner, result)
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
        pending = coordinator.record(planner, result)

        def mutate(action):
            action["arguments"]["text"] = "changed by UI"
            return True

        approved = coordinator.approve(
            planner, pending.pending_id, actor="owner", session_id="cli-session", confirm=mutate
        )
        self.assertTrue(approved.ok)

    def test_pending_capacity_is_bounded(self):
        planner = build()
        result = planner.run("Write note")
        coordinator = ApprovalCoordinator(max_pending=1)
        coordinator.record(planner, result)
        with self.assertRaises(RuntimeError):
            coordinator.record(planner, result)


if __name__ == "__main__":
    unittest.main()
