import unittest

from core.agent_org import (
    AgentOrganization, Risk, TaskStatus, build_elite_council, build_role_catalog,
)


class AgentOrganizationTests(unittest.TestCase):
    def test_role_catalog_has_at_least_one_hundred_distinct_roles(self):
        roles = build_role_catalog()
        self.assertGreaterEqual(len(roles), 100)
        self.assertEqual(len({role.role_id for role in roles}), len(roles))

    def test_elite_council_has_exactly_ten_roles(self):
        council = build_elite_council()
        self.assertEqual(len(council), 10)
        self.assertTrue(all(role.tier == "elite" for role in council))

    def test_org_chart_does_not_claim_roles_are_running_agents(self):
        chart = AgentOrganization().organization_chart()
        self.assertEqual(chart["specialist_role_count"], 120)
        self.assertEqual(chart["active_llm_processes"], 0)
        self.assertIn("not running agents", chart["note"])

    def test_unknown_role_rejected(self):
        org = AgentOrganization()
        with self.assertRaises(ValueError):
            org.create_task(title="Test", objective="Test", role_id="root_shell_admin")

    def test_missing_handler_blocks_task(self):
        org = AgentOrganization()
        task = org.create_task(title="Audit", objective="Review risks", role_id="security_senior")
        result = org.dispatch(task.task_id)
        self.assertFalse(result.ok)
        self.assertEqual(result.task.status, TaskStatus.BLOCKED)

    def test_read_only_task_dispatches_to_registered_worker(self):
        org = AgentOrganization(handlers={"research_senior": lambda task: "Three sources reviewed"})
        task = org.create_task(title="Research", objective="Find sources", role_id="research_senior")
        result = org.dispatch(task.task_id)
        self.assertTrue(result.ok)
        self.assertEqual(result.task.status, TaskStatus.SUCCEEDED)
        self.assertEqual(result.task.result_summary, "Three sources reviewed")

    def test_risky_task_fails_closed_without_approver(self):
        org = AgentOrganization(handlers={"sales_senior": lambda task: "Sent email"})
        task = org.create_task(title="Outreach", objective="Send prospect email",
                               role_id="sales_senior", risk=Risk.EXTERNAL)
        result = org.dispatch(task.task_id)
        self.assertFalse(result.ok)
        self.assertEqual(result.task.status, TaskStatus.BLOCKED)

    def test_risky_task_requires_explicit_true_approval(self):
        called = []
        org = AgentOrganization(
            handlers={"finance_lead": lambda task: "Payment simulated"},
            approver=lambda task: called.append(task.task_id) or True,
        )
        task = org.create_task(title="Budget", objective="Review a simulated financial action",
                               role_id="finance_lead", risk=Risk.FINANCIAL)
        result = org.dispatch(task.task_id)
        self.assertTrue(result.ok)
        self.assertEqual(called, [task.task_id])

    def test_approver_exception_fails_closed(self):
        def broken(_task):
            raise RuntimeError("sensitive detail")
        org = AgentOrganization(handlers={"devops_senior": lambda task: "done"}, approver=broken)
        task = org.create_task(title="Deploy", objective="Deploy to production",
                               role_id="devops_senior", risk=Risk.EXTERNAL)
        result = org.dispatch(task.task_id)
        self.assertFalse(result.ok)
        self.assertEqual(result.task.status, TaskStatus.BLOCKED)

    def test_handler_exception_does_not_leak_details(self):
        def broken(_task):
            raise RuntimeError("secret-token-123")
        org = AgentOrganization(handlers={"security_senior": broken})
        task = org.create_task(title="Audit", objective="Review the config", role_id="security_senior")
        result = org.dispatch(task.task_id)
        self.assertFalse(result.ok)
        self.assertNotIn("secret-token-123", result.message)
        self.assertEqual(result.task.status, TaskStatus.FAILED)

    def test_task_capacity_is_bounded(self):
        org = AgentOrganization(max_tasks=1)
        org.create_task(title="One", objective="One", role_id="qa_senior")
        with self.assertRaises(OverflowError):
            org.create_task(title="Two", objective="Two", role_id="qa_senior")

    def test_invalid_risk_rejected(self):
        org = AgentOrganization()
        with self.assertRaises(ValueError):
            org.create_task(title="Task", objective="Goal", role_id="qa_senior", risk="high")


if __name__ == "__main__":
    unittest.main()
