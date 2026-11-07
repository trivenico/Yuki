import unittest
from types import SimpleNamespace

from automation.coordinator_reviewer_authorization import coordinator_reviewer_authorizer


class CoordinatorReviewerAuthorizationTests(unittest.TestCase):
    def setUp(self):
        self.agent = SimpleNamespace(
            enabled=True,
            permissions=frozenset({"verification:submit", "verification:review"}),
        )
        self.calls = []
        self.coordinator = SimpleNamespace(
            agents={"reviewer-a": self.agent},
            _allowed=lambda actor, action, resource: self.calls.append(
                (actor, action, resource)
            ) or True,
        )
        self.authorize = coordinator_reviewer_authorizer(self.coordinator, resolve_principal=lambda agent_id: "principal-" + agent_id)

    def test_checks_registered_agent_and_current_coordinator_policy(self):
        digest = "a" * 64
        self.assertTrue(self.authorize("reviewer-a", "verification:review", digest))
        self.assertEqual(self.calls, [("reviewer-a", "verification:review", digest)])

    def test_rejects_unknown_agent_and_unknown_action(self):
        self.assertFalse(self.authorize("not-registered", "verification:review", "a" * 64))
        self.assertFalse(self.authorize("reviewer-a", "admin", "a" * 64))
        self.assertEqual(self.calls, [])

    def test_rejects_disabled_agent_or_missing_permission(self):
        self.agent.enabled = False
        self.assertFalse(self.authorize("reviewer-a", "verification:review", "a" * 64))
        self.agent.enabled = True
        self.agent.permissions = frozenset()
        self.assertFalse(self.authorize("reviewer-a", "verification:submit", "a" * 64))
        self.assertEqual(self.calls, [])

    def test_policy_errors_fail_closed(self):
        self.coordinator._allowed = lambda *args: (_ for _ in ()).throw(RuntimeError("policy down"))
        self.assertFalse(self.authorize("reviewer-a", "verification:review", "a" * 64))


if __name__ == "__main__":
    unittest.main()
