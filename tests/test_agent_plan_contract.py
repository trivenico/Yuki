import unittest

from automation.agent_plan_contract import agent_plan_request_digest


class AgentPlanDigestContractTests(unittest.TestCase):
    def setUp(self):
        self.subtasks = [{
            "subtask_id": "research",
            "agent_id": "researcher",
            "action": "emit",
            "description": " Gather evidence ",
            "required_capabilities": ["research", "summarize"],
            "required_permissions": ["web:read"],
            "depends_on": [],
            "requires_approval": True,
        }]

    def digest(self, *, tenant="tenant-a", owner="human-a", subtasks=None):
        return agent_plan_request_digest(
            tenant_id=tenant,
            owner_principal_id=owner,
            subtasks=self.subtasks if subtasks is None else subtasks,
        )

    def test_digest_is_stable_across_collection_order_and_aliases(self):
        reordered = [{
            "subtask_id": "research",
            "agent_id": "researcher",
            "description": "Gather evidence",
            "capabilities": ["summarize", "research"],
            "permissions": ["web:read"],
            "dependencies": [],
            "requires_approval": True,
        }]
        self.assertEqual(self.digest(), self.digest(subtasks=reordered))

    def test_digest_binds_tenant_and_owner(self):
        baseline = self.digest()
        self.assertNotEqual(baseline, self.digest(tenant="tenant-b"))
        self.assertNotEqual(baseline, self.digest(owner="human-b"))

    def test_digest_binds_selected_agent_action_and_approval(self):
        baseline = self.digest()
        changed = [dict(self.subtasks[0], action="different")]
        self.assertNotEqual(baseline, self.digest(subtasks=changed))
        changed = [dict(self.subtasks[0], requires_approval=False)]
        self.assertNotEqual(baseline, self.digest(subtasks=changed))

    def test_duplicate_subtasks_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "duplicate_subtask"):
            self.digest(subtasks=self.subtasks * 2)


if __name__ == "__main__":
    unittest.main()
