import unittest
from types import SimpleNamespace
from unittest.mock import patch

from core.agent import YukiCoreAgent


class Skill:
    name = "triage"
    description = "Triage a task"
    risk = "low"
    requires_approval = False


class Registry:
    def __init__(self):
        self.skill = Skill()

    def get(self, name):
        return self.skill if name == "triage" else None

    def names(self):
        return ("triage",)


class Gate:
    def __init__(self, approved=True):
        self.approved = approved
        self.calls = []

    def request(self, **kwargs):
        self.calls.append(kwargs)
        return self.approved


class AgentInvocationObserverTests(unittest.TestCase):
    def test_observer_runs_after_permission_approval_and_before_skill_invocation(self):
        order = []
        gate = Gate(True)
        agent = YukiCoreAgent(registry=Registry(), permission_gate=gate)
        observer = lambda: order.append("observer")

        def invoke(*args, **kwargs):
            callback = kwargs.get("before_execute")
            if callback is not None:
                callback()
            order.append("skill")
            return SimpleNamespace(ok=True, message="done", data=None)

        with patch("core.agent.invoke_registered_skill", side_effect=invoke):
            response = agent.run_skill("triage", "review this", before_invoke=observer)

        self.assertTrue(response.ok)
        self.assertEqual(order, ["observer", "skill"])
        self.assertEqual(len(gate.calls), 1)

    def test_observer_failure_fails_closed_before_skill_execution(self):
        gate = Gate(True)
        agent = YukiCoreAgent(registry=Registry(), permission_gate=gate)
        with patch("core.agent.invoke_registered_skill") as invoke:
            response = agent.run_skill(
                "triage", "review this",
                before_invoke=lambda: (_ for _ in ()).throw(RuntimeError("telemetry unavailable")),
            )
        self.assertFalse(response.ok)
        self.assertEqual(response.stage, "pre_execution_observer_failed")
        invoke.assert_called_once()
        # The guarded runtime owns the callback and returns before execute().
        # No skill execution occurs when the evidence write fails.

    def test_observer_is_not_called_when_permission_is_denied(self):
        order = []
        gate = Gate(False)
        agent = YukiCoreAgent(registry=Registry(), permission_gate=gate)
        with patch("core.agent.invoke_registered_skill") as invoke:
            response = agent.run_skill(
                "triage", "review this",
                before_invoke=lambda: order.append("observer"),
            )
        self.assertFalse(response.ok)
        self.assertEqual(response.stage, "permission_denied")
        self.assertEqual(order, [])
        invoke.assert_not_called()


if __name__ == "__main__":
    unittest.main()
