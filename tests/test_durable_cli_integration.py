"""CLI-level tests proving durable tasks are wired through the approved skill runtime."""
from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import yuki
from core.approved_skill_runtime import ApprovedSkillRuntime
from core.approved_skill_wiring import SkillApprovalStore
from core.guarded_skill_runtime import OneShotSkillApprovals


class Registry:
    def __init__(self, *skills):
        self.skills = {skill.name: skill for skill in skills}

    def get(self, name):
        return self.skills.get(name)

    def describe(self):
        return [
            {"name": skill.name, "description": "test skill", "risk": skill.risk}
            for skill in self.skills.values()
        ]


class LowRiskSkill:
    name = "read_demo"
    risk = "low"
    requires_approval = False

    def __init__(self):
        self.calls = []

    def execute(self, argument=""):
        self.calls.append(argument)
        return {"ok": True, "message": "executed safely"}


class HighRiskSkill:
    name = "write_demo"
    risk = "high"
    requires_approval = True

    def __init__(self):
        self.calls = []

    def execute(self, argument=""):
        self.calls.append(argument)
        return {"ok": True, "message": "write completed"}


class DummyAgent:
    def ask(self, _message):
        return "unused"

    def status(self):
        return "test"


class DurableCliIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.db = root / "durable.sqlite"
        self.low = LowRiskSkill()
        self.high = HighRiskSkill()
        self.registry = Registry(self.low, self.high)
        self.approvals = OneShotSkillApprovals()
        self.approval_store = SkillApprovalStore(root / "approvals.sqlite")

        # Mirror the real CLI lifecycle: each command rebuilds the registry
        # and runtime facade, while one-shot approvals are shared process state.
        # This catches accidental reliance on retaining one runtime instance
        # between the /skill approve and /durable run commands.
        def build_runtime():
            runtime = ApprovedSkillRuntime(
                self.registry,
                self.approval_store,
                invocation_approvals=self.approvals,
            )
            return self.registry, runtime

        self.patches = [
            patch.object(yuki, "_DURABLE_TASK_DB", self.db),
            patch.object(yuki, "_build_approved_skill_runtime", build_runtime),
        ]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)

    def command(self, message):
        return yuki.handle_command(message, [], [], DummyAgent(), None)

    def test_goal_submit_list_and_run_are_wired_end_to_end(self):
        created = self.command("/durable goal create")
        goal_id = created.split(": ", 1)[1]
        submitted = self.command(
            f"/durable task submit integration-key | {goal_id} | read_demo | hello"
        )
        self.assertIn("is queued", submitted)
        listing = self.command(f"/durable task list {goal_id}")
        self.assertIn("skill.invoke", listing)
        self.assertIn("queued", listing)
        ran = self.command("/durable run")
        self.assertIn("succeeded", ran)
        self.assertEqual(self.low.calls, ["hello"])
        self.assertIn("succeeded", self.command(f"/durable task list {goal_id}"))

    def test_high_risk_task_requires_approval_bound_to_exact_argument(self):
        goal_id = self.command("/durable goal create").split(": ", 1)[1]
        self.command(f"/durable task submit high-key | {goal_id} | write_demo | approved-value")
        # Wrong argument approval must not authorize the queued task.
        self.command("/skill approve write_demo different-value")
        first = self.command("/durable run")
        self.assertIn("failed", first)
        self.assertEqual(self.high.calls, [])

        # The failed task terminally completed its goal; use a new goal for the retry.
        second_goal_id = self.command("/durable goal create").split(": ", 1)[1]
        self.command(f"/durable task submit high-key-2 | {second_goal_id} | write_demo | approved-value")
        self.command("/skill approve write_demo approved-value")
        second = self.command("/durable run")
        self.assertIn("succeeded", second)
        self.assertEqual(self.high.calls, ["approved-value"])

    def test_unknown_skill_cannot_be_queued(self):
        goal_id = self.command("/durable goal create").split(": ", 1)[1]
        response = self.command(f"/durable task submit unknown-key | {goal_id} | shell | whoami")
        self.assertIn("unregistered skill", response.lower())


if __name__ == "__main__":
    unittest.main()
