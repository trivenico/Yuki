import json
import sys
import unittest

from core.action_approvals import ActionApprovalStore
from core.agent import YukiCoreAgent
from core.manifest_task_agent import ManifestTaskAgent
from core.skill_dispatch import SkillDispatcher
from core.skill_manifest import SkillManifest
from core.subprocess_executor import SubprocessExecutor


class FakeProvider:
    def __init__(self, outputs):
        self.outputs = list(outputs)

    def generate(self, prompt):
        return self.outputs.pop(0)


def make_manifest(skill_id="notes.read", capabilities=None, risk="low"):
    return SkillManifest.from_dict({
        "skill_id": skill_id,
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
        "capabilities": capabilities or ["workspace.read"],
        "risk": risk,
        "timeout_seconds": 3,
        "max_output_bytes": 1024,
    })


def build_dispatcher(manifest, script=None):
    store = ActionApprovalStore()
    dispatcher = SkillDispatcher(
        capability_grants=set(manifest.capabilities),
        approval_store=store,
    )
    worker_script = script or (
        "import json,sys; json.load(sys.stdin); "
        "print(json.dumps({'ok':True,'result':{'output':{'saved':True}}}))"
    )
    dispatcher.register_subprocess(
        manifest, [sys.executable, "-c", worker_script],
        executor=SubprocessExecutor(timeout_seconds=3),
    )
    return dispatcher


class ManifestTaskAgentTests(unittest.TestCase):
    def test_plans_and_runs_only_manifest_registered_skill(self):
        manifest = make_manifest()
        dispatcher = build_dispatcher(manifest)
        agent = YukiCoreAgent(dispatcher=dispatcher)
        provider = FakeProvider([
            json.dumps({"type": "tool_call", "skill_id": "notes.read", "arguments": {"text": "hello"}}),
            json.dumps({"type": "final", "message": "Done."}),
        ])
        result = ManifestTaskAgent(provider, agent, dispatcher, max_steps=2).run("Read notes")
        self.assertTrue(result.ok)
        self.assertEqual(result.status, "completed")

    def test_side_effecting_action_pauses_for_approval_without_execution(self):
        manifest = make_manifest("notes.write", ["workspace.write"], "low")
        script = "import sys; sys.exit(99)"
        dispatcher = build_dispatcher(manifest, script)
        agent = YukiCoreAgent(dispatcher=dispatcher)
        provider = FakeProvider([
            json.dumps({"type": "tool_call", "skill_id": "notes.write", "arguments": {"text": "hello"}})
        ])
        result = ManifestTaskAgent(provider, agent, dispatcher).run("Write a note")
        self.assertEqual(result.status, "approval_required")
        self.assertEqual(result.pending_skill, "notes.write")
        self.assertEqual(result.pending_arguments, {"text": "hello"})

    def test_unknown_skill_never_executes(self):
        manifest = make_manifest()
        dispatcher = build_dispatcher(manifest)
        agent = YukiCoreAgent(dispatcher=dispatcher)
        provider = FakeProvider([
            json.dumps({"type": "tool_call", "skill_id": "shell.run", "arguments": {"text": "id"}})
        ])
        result = ManifestTaskAgent(provider, agent, dispatcher).run("Run shell")
        self.assertEqual(result.status, "blocked")

    def test_resume_requires_valid_action_bound_approval(self):
        manifest = make_manifest("notes.write", ["workspace.write"], "low")
        dispatcher = build_dispatcher(manifest)
        agent = YukiCoreAgent(dispatcher=dispatcher)
        planner = ManifestTaskAgent(FakeProvider([]), agent, dispatcher)
        args = {"text": "hello"}
        token = dispatcher.issue_approval(
            "notes.write", args, actor="owner", session_id="session-1", approved=True
        )
        result = planner.resume_approved(
            "notes.write", args, approval_token=token.token,
            actor="owner", session_id="session-1",
        )
        self.assertTrue(result.ok)
        self.assertEqual(result.status, "completed")

    def test_resume_rejects_approval_for_different_arguments(self):
        manifest = make_manifest("notes.write", ["workspace.write"], "low")
        dispatcher = build_dispatcher(manifest)
        agent = YukiCoreAgent(dispatcher=dispatcher)
        planner = ManifestTaskAgent(FakeProvider([]), agent, dispatcher)
        token = dispatcher.issue_approval(
            "notes.write", {"text": "original"}, actor="owner",
            session_id="session-1", approved=True
        )
        result = planner.resume_approved(
            "notes.write", {"text": "changed"}, approval_token=token.token,
            actor="owner", session_id="session-1",
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.status, "tool_failed")


if __name__ == "__main__":
    unittest.main()
