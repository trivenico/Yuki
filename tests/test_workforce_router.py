import unittest

from core.durable_task_engine import DurableTaskEngine
from core.workforce import AgentProfile, AgentRegistry
from core.workforce_router import NoEligibleAgent, WorkforceTaskRouter


class WorkforceTaskRouterTests(unittest.TestCase):
    def setUp(self):
        self.engine = DurableTaskEngine(":memory:")
        self.addCleanup(self.engine.close)
        self.goal_id = self.engine.create_goal()
        self.employee = AgentProfile(
            employee_id="ENG-TEST-001",
            name="Backend Specialist",
            department="Engineering",
            role="Backend Engineer",
            capabilities=frozenset({"python", "api_design"}),
            reports_to="YUKI-CEO",
            permissions=frozenset({"repo:read"}),
        )
        self.registry = AgentRegistry([self.employee])
        self.router = WorkforceTaskRouter(self.registry, self.engine)

    def test_assigns_employee_and_persists_unapproved_queue_item(self):
        assignment = self.router.submit(
            goal_id=self.goal_id,
            description="Review the API handler",
            required_capabilities={"python", "api_design"},
            required_permissions={"repo:read"},
            idempotency_key="api-review-1",
        )
        queued = self.engine.get_task(assignment.task_id)
        self.assertEqual(assignment.employee_id, self.employee.employee_id)
        self.assertEqual(queued["state"], "queued")
        self.assertEqual(queued["payload"]["assigned_employee_id"], self.employee.employee_id)
        self.assertEqual(queued["payload"]["required_permissions"], ["repo:read"])
        self.assertIs(queued["payload"]["execution_authorized"], False)
        self.assertIs(queued["payload"]["review_required"], False)


    def test_can_require_independent_review(self):
        assignment = self.router.submit(
            goal_id=self.goal_id,
            description="Implement a feature and verify it",
            required_capabilities={"python"},
            idempotency_key="review-required",
            review_required=True,
        )
        queued = self.engine.get_task(assignment.task_id)
        self.assertIs(queued["payload"]["review_required"], True)

    def test_review_required_must_be_boolean(self):
        with self.assertRaises(ValueError):
            self.router.submit(
                goal_id=self.goal_id,
                description="Review output",
                required_capabilities={"python"},
                idempotency_key="bad-review-flag",
                review_required="yes",
            )

    def test_rejects_task_when_employee_lacks_required_permission(self):
        with self.assertRaises(NoEligibleAgent):
            self.router.submit(
                goal_id=self.goal_id,
                description="Write to the repository",
                required_capabilities={"python"},
                required_permissions={"repo:write"},
                idempotency_key="repo-write-1",
            )

    def test_rejects_task_when_capability_is_missing(self):
        with self.assertRaises(NoEligibleAgent):
            self.router.submit(
                goal_id=self.goal_id,
                description="Design a mobile application",
                required_capabilities={"ios"},
                idempotency_key="mobile-1",
            )

    def test_rejects_retryable_workforce_submissions(self):
        with self.assertRaises(ValueError):
            self.router.submit(
                goal_id=self.goal_id,
                description="Potentially side-effecting task",
                required_capabilities={"python"},
                idempotency_key="retry-unsafe",
                max_attempts=2,
            )

    def test_rejects_invalid_description_and_autonomy(self):
        for kwargs in (
            {"description": " "},
            {"max_autonomy_tier": 4},
        ):
            args = {
                "goal_id": self.goal_id,
                "description": "Valid description",
                "required_capabilities": {"python"},
                "idempotency_key": "invalid-" + str(len(kwargs)),
            }
            args.update(kwargs)
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.router.submit(**args)


if __name__ == "__main__":
    unittest.main()
