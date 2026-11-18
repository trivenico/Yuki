import unittest
from types import SimpleNamespace

from core.workforce_executor import YukiAgentWorkforceExecutor


class Registry:
    def __init__(self, skills):
        self._skills = {skill.name: skill for skill in skills}

    def names(self):
        return tuple(sorted(self._skills))

    def get(self, name):
        return self._skills.get(name)


class FakeAgent:
    def __init__(self, skills, response=None):
        self.registry = Registry(skills)
        self.response = response or SimpleNamespace(ok=True, stage="report", message="approved and completed")
        self.calls = []

    def run_skill(self, name, argument):
        self.calls.append((name, argument))
        return self.response


class WorkforceExecutorTests(unittest.TestCase):
    def setUp(self):
        self.skill = SimpleNamespace(
            name="repo_reader",
            capabilities=("repository_read", "code_review"),
            permissions=("repo:read",),
        )
        self.agent = FakeAgent([self.skill])
        self.executor = YukiAgentWorkforceExecutor(self.agent)
        self.employee = SimpleNamespace(autonomy_tier=1)

    def execute(self, **overrides):
        values = {
            "employee": self.employee,
            "description": "Review this pull request",
            "required_capabilities": ("code_review",),
            "required_permissions": ("repo:read",),
            "max_autonomy_tier": 1,
            "task_id": "task-123",
        }
        values.update(overrides)
        return self.executor.authorize_and_execute(**values)

    def test_delegates_to_yuki_agent_approval_gated_run_skill(self):
        result = self.execute()
        self.assertTrue(result.ok)
        self.assertEqual(result.status, "report")
        self.assertEqual(self.agent.calls, [("repo_reader", "Review this pull request")])

    def test_missing_skill_scope_metadata_fails_closed(self):
        self.agent.registry = Registry([SimpleNamespace(name="unknown_scope")])
        result = self.execute()
        self.assertFalse(result.ok)
        self.assertEqual(result.status, "no_scope_compatible_skill")
        self.assertEqual(self.agent.calls, [])

    def test_permission_scope_mismatch_fails_closed(self):
        self.skill.permissions = ("repo:write",)
        result = self.execute()
        self.assertFalse(result.ok)
        self.assertEqual(result.status, "no_scope_compatible_skill")
        self.assertEqual(self.agent.calls, [])

    def test_agent_approval_denial_is_preserved(self):
        self.agent.response = SimpleNamespace(ok=False, stage="permission_denied", message="approval not granted")
        result = self.execute()
        self.assertFalse(result.ok)
        self.assertEqual(result.status, "permission_denied")

    def test_autonomy_limit_is_rechecked(self):
        result = self.execute(max_autonomy_tier=0)
        self.assertFalse(result.ok)
        self.assertEqual(result.status, "autonomy_denied")
        self.assertEqual(self.agent.calls, [])


if __name__ == "__main__":
    unittest.main()
