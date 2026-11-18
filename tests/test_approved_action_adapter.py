"""Security tests for explicit approved-action to registered-skill dispatch."""
import unittest
from types import SimpleNamespace

from core.approved_action_adapter import ApprovedActionAdapter, ApprovedActionBinding
from core.agent import YukiCoreAgent
from core.permissions import PermissionGate
from skills.base import Skill, SkillResult
from core.agent import YukiCoreAgent
from core.permissions import PermissionGate
from skills.base import Skill, SkillResult


class Registry:
    def __init__(self, skill):
        self.skill = skill

    def get(self, name):
        return self.skill if name == self.skill.name else None


class Agent:
    def __init__(self, skill):
        self.registry = Registry(skill)
        self.calls = []

    def run_skill(self, name, argument):
        self.calls.append((name, argument))
        return SimpleNamespace(ok=True, message="permission-gated")


class ApprovedActionAdapterTests(unittest.TestCase):
    def setUp(self):
        self.skill = SimpleNamespace(name="notes.write", risk="medium")
        self.agent = Agent(self.skill)
        self.adapter = ApprovedActionAdapter(self.agent, {
            "notes.write": ApprovedActionBinding(
                "notes.write", lambda args: args["text"]
            )
        })

    def test_dispatches_only_explicitly_bound_action_through_run_skill(self):
        result = self.adapter.execute({
            "skill_id": "notes.write", "arguments": {"text": "hello"}
        })
        self.assertTrue(result.ok)
        self.assertEqual(self.agent.calls, [("notes.write", "hello")])

    def test_real_agent_requires_fresh_permission_gate_approval(self):
        class WriteSkill(Skill):
            name = "notes_write"
            description = "Write a test note"
            risk = "medium"
            requires_approval = True

            def __init__(self):
                self.invocations = []

            def run(self, argument=""):
                self.invocations.append(argument)
                return SkillResult(True, "written")

        skill = WriteSkill()
        requests = []
        agent = YukiCoreAgent(
            skills=[skill],
            permission_gate=PermissionGate(lambda request: requests.append(request) or True),
        )
        adapter = ApprovedActionAdapter(agent, {
            "notes_write": ApprovedActionBinding("notes_write", lambda args: args["text"])
        })
        result = adapter.execute({
            "skill_id": "notes_write", "arguments": {"text": "exact approved text"}
        })
        self.assertTrue(result.ok)
        self.assertEqual(skill.invocations, ["exact approved text"])
        self.assertEqual(len(requests), 1)
        self.assertIn("sha256:", requests[0].action)

    def test_real_agent_permission_gate_denial_prevents_skill_execution(self):
        class WriteSkill(Skill):
            name = "notes_write"
            description = "Write a test note"
            risk = "medium"

            def __init__(self):
                self.invocations = []

            def run(self, argument=""):
                self.invocations.append(argument)
                return SkillResult(True, "written")

        skill = WriteSkill()
        requests = []
        agent = YukiCoreAgent(
            registry=SkillRegistry([skill]),
            permission_gate=PermissionGate(lambda request: requests.append(request) or False),
        )
        adapter = ApprovedActionAdapter(agent, {
            "notes_write": ApprovedActionBinding("notes_write", lambda args: args["text"])
        })
        result = adapter.execute({
            "skill_id": "notes_write", "arguments": {"text": "must not execute"}
        })
        self.assertFalse(result.ok)
        self.assertEqual(result.stage, "permission_denied")
        self.assertEqual(skill.invocations, [])
        self.assertEqual(len(requests), 1)

    def test_unknown_action_id_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "not explicitly configured"):
            self.adapter.execute({"skill_id": "shell.exec", "arguments": {}})
        self.assertEqual(self.agent.calls, [])

    def test_extra_action_fields_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "only skill_id and arguments"):
            self.adapter.execute({
                "skill_id": "notes.write", "arguments": {"text": "x"},
                "executor": "arbitrary",
            })
        self.assertEqual(self.agent.calls, [])

    def test_malformed_arguments_fail_closed(self):
        with self.assertRaises(KeyError):
            self.adapter.execute({"skill_id": "notes.write", "arguments": {}})
        self.assertEqual(self.agent.calls, [])

    def test_changed_registry_identity_is_rejected(self):
        self.agent.registry.skill = SimpleNamespace(name="notes.write", risk="medium")
        with self.assertRaisesRegex(RuntimeError, "identity changed"):
            self.adapter.execute({
                "skill_id": "notes.write", "arguments": {"text": "hello"}
            })
        self.assertEqual(self.agent.calls, [])

    def test_replaced_agent_registry_is_rejected(self):
        self.agent.registry = Registry(SimpleNamespace(name="notes.write", risk="medium"))
        with self.assertRaisesRegex(RuntimeError, "registry changed"):
            self.adapter.execute({
                "skill_id": "notes.write", "arguments": {"text": "hello"}
            })
        self.assertEqual(self.agent.calls, [])


    def test_changed_skill_risk_is_rejected(self):
        self.skill.risk = "low"
        with self.assertRaisesRegex(RuntimeError, "risk changed"):
            self.adapter.execute({"skill_id": "notes.write", "arguments": {"text": "hello"}})
        self.assertEqual(self.agent.calls, [])

    def test_unsupported_risk_is_rejected_at_configuration(self):
        critical = SimpleNamespace(name="dangerous", risk="critical")
        with self.assertRaisesRegex(ValueError, "risk level"):
            ApprovedActionAdapter(Agent(critical), {
                "dangerous": ApprovedActionBinding("dangerous", lambda _: "")
            })


    def test_real_agent_permission_gate_is_rechecked_before_skill_execution(self):
        class RecordingSkill(Skill):
            name = "notes.write"
            description = "Write a note"
            risk = "medium"
            requires_approval = True

            def __init__(self):
                self.executions = []

            def run(self, argument=""):
                self.executions.append(argument)
                return SkillResult(True, "written")

        skill = RecordingSkill()
        requests = []
        agent = YukiCoreAgent(
            skills=[skill],
            permission_gate=PermissionGate(
                approver=lambda request: requests.append(request) or False
            ),
        )
        adapter = ApprovedActionAdapter(agent, {
            "notes.write": ApprovedActionBinding("notes.write", lambda args: args["text"])
        })
        with self.assertRaisesRegex(PermissionError, "permission_denied"):
            adapter.execute({"skill_id": "notes.write", "arguments": {"text": "hello"}})
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0].action, "skill:notes.write:sha256:2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824")
        self.assertEqual(skill.executions, [])

    def test_real_agent_executes_only_after_permission_gate_and_one_shot_guard(self):
        class RecordingSkill(Skill):
            name = "notes.write"
            description = "Write a note"
            risk = "medium"
            requires_approval = True

            def __init__(self):
                self.executions = []

            def run(self, argument=""):
                self.executions.append(argument)
                return SkillResult(True, "written")

        skill = RecordingSkill()
        requests = []
        agent = YukiCoreAgent(
            skills=[skill],
            permission_gate=PermissionGate(
                approver=lambda request: requests.append(request) or True
            ),
        )
        adapter = ApprovedActionAdapter(agent, {
            "notes.write": ApprovedActionBinding("notes.write", lambda args: args["text"])
        })
        result = adapter.execute({"skill_id": "notes.write", "arguments": {"text": "hello"}})
        self.assertTrue(result.ok)
        self.assertEqual(len(requests), 1)
        self.assertEqual(skill.executions, ["hello"])

    def test_decoder_must_return_bounded_text(self):
        bad = ApprovedActionAdapter(self.agent, {
            "notes.write": ApprovedActionBinding("notes.write", lambda _: {"shell": "x"})
        })
        with self.assertRaisesRegex(ValueError, "must be text"):
            bad.execute({"skill_id": "notes.write", "arguments": {}})
        self.assertEqual(self.agent.calls, [])


if __name__ == "__main__":
    unittest.main()
