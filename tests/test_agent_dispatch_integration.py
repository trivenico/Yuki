import sys
import unittest

from core.agent import YukiCoreAgent
from core.action_approvals import ActionApprovalStore
from core.skill_dispatch import SkillDispatcher
from core.skill_manifest import SkillManifest
from core.subprocess_executor import SubprocessExecutor


def manifest():
    return SkillManifest.from_dict({
        "skill_id": "test.worker",
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
    })


class AgentDispatcherIntegrationTests(unittest.TestCase):
    def build_agent(self):
        store = ActionApprovalStore()
        dispatcher = SkillDispatcher(
            capability_grants={"workspace.write"},
            approval_store=store,
        )
        script = (
            "import json,sys; json.load(sys.stdin); "
            "print(json.dumps({'ok':True,'result':{'output':{'saved':True}}}))"
        )
        dispatcher.register_subprocess(
            manifest(), [sys.executable, "-c", script],
            executor=SubprocessExecutor(timeout_seconds=3),
        )
        return YukiCoreAgent(dispatcher=dispatcher), dispatcher

    def test_agent_entrypoint_denies_missing_approval(self):
        agent, _ = self.build_agent()
        response = agent.dispatch_manifest_skill("test.worker", {"text": "hello"})
        self.assertFalse(response.ok)
        self.assertEqual(response.stage, "blocked")
        self.assertIn("approval_token_required", response.message)

    def test_agent_entrypoint_executes_only_with_action_bound_approval(self):
        agent, dispatcher = self.build_agent()
        args = {"text": "hello"}
        token = dispatcher.issue_approval(
            "test.worker", args, actor="owner", session_id="test-session", approved=True
        )
        response = agent.dispatch_manifest_skill(
            "test.worker", args, approval_token=token.token,
            actor="owner", session_id="test-session",
        )
        self.assertTrue(response.ok)
        self.assertEqual(response.data, {"saved": True})

    def test_agent_entrypoint_fails_closed_without_dispatcher(self):
        agent = YukiCoreAgent()
        response = agent.dispatch_manifest_skill("test.worker", {"text": "hello"})
        self.assertFalse(response.ok)
        self.assertEqual(response.stage, "blocked")


if __name__ == "__main__":
    unittest.main()
