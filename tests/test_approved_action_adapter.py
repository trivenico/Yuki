"""Security tests for explicit approved-action to registered-skill dispatch."""
import unittest
from types import SimpleNamespace

from core.approved_action_adapter import ApprovedActionAdapter, ApprovedActionBinding


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

    def test_unsupported_risk_is_rejected_at_configuration(self):
        critical = SimpleNamespace(name="dangerous", risk="critical")
        with self.assertRaisesRegex(ValueError, "risk level"):
            ApprovedActionAdapter(Agent(critical), {
                "dangerous": ApprovedActionBinding("dangerous", lambda _: "")
            })

    def test_decoder_must_return_bounded_text(self):
        bad = ApprovedActionAdapter(self.agent, {
            "notes.write": ApprovedActionBinding("notes.write", lambda _: {"shell": "x"})
        })
        with self.assertRaisesRegex(ValueError, "must be text"):
            bad.execute({"skill_id": "notes.write", "arguments": {}})
        self.assertEqual(self.agent.calls, [])


if __name__ == "__main__":
    unittest.main()
