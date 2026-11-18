import unittest

from core.durable_task_engine import DurableTaskEngine
from core.workforce import AgentProfile, AgentRegistry
from core.workforce_worker import WorkforceWorker


class Outcome:
    def __init__(self, ok, status):
        self.ok = ok
        self.status = status


class Executor:
    def __init__(self, outcome=None):
        self.outcome = outcome or Outcome(True, "completed")
        self.calls = []

    def authorize_and_execute(self, **kwargs):
        self.calls.append(kwargs)
        return self.outcome


class WorkforceWorkerTests(unittest.TestCase):
    def setUp(self):
        self.engine = DurableTaskEngine(":memory:")
        self.addCleanup(self.engine.close)
        self.goal = self.engine.create_goal()
        self.employee = AgentProfile(
            employee_id="ENG-001", name="Backend Specialist",
            department="Engineering", role="Backend Engineer",
            capabilities=frozenset({"python", "api_design"}),
            reports_to="YUKI-CEO", permissions=frozenset({"repo:read"}),
        )
        self.registry = AgentRegistry([self.employee])
        self.executor = Executor()
        self.worker = WorkforceWorker(
            self.engine, self.registry, worker_id="workforce-worker",
            executor=self.executor,
        )

    def submit(self, *, max_attempts=1, payload=None, task_type="workforce.task"):
        body = payload or {
            "description": "Review API code",
            "assigned_employee_id": self.employee.employee_id,
            "required_capabilities": ["python", "api_design"],
            "required_permissions": ["repo:read"],
            "max_autonomy_tier": 1,
            "execution_authorized": False,
        }
        return self.engine.submit(
            self.goal, task_type, body, idempotency_key="workforce-test",
            max_attempts=max_attempts,
        )

    def test_worker_leaves_other_task_types_queued(self):
        unrelated = self.engine.submit(
            self.goal, "skill.invoke", {"skill_name": "safe", "argument": ""},
            idempotency_key="unrelated", max_attempts=1,
        )
        workforce = self.submit()
        result = self.worker.run_once()
        self.assertEqual(result["task_id"], workforce["id"])
        self.assertEqual(self.engine.get_task(workforce["id"])["state"], "succeeded")
        self.assertEqual(self.engine.get_task(unrelated["id"])["state"], "queued")

    def test_dispatches_only_after_revalidating_profile_and_delegates_approval(self):
        task = self.submit()
        result = self.worker.run_once()
        self.assertEqual(result["state"], "succeeded")
        self.assertEqual(len(self.executor.calls), 1)
        self.assertEqual(self.executor.calls[0]["employee"], self.employee)
        self.assertEqual(self.executor.calls[0]["required_permissions"], ("repo:read",))
        self.assertEqual(self.engine.get_task(task["id"])["result"]["employee_id"], "ENG-001")

    def test_rejects_retryable_side_effect_task_without_execution(self):
        task = self.submit(max_attempts=3)
        result = self.worker.run_once()
        self.assertEqual(result["status"], "unsafe_retry_policy")
        self.assertEqual(self.engine.get_task(task["id"])["state"], "failed")
        self.assertEqual(self.executor.calls, [])

    def test_rejects_employee_permission_drift(self):
        task = self.submit()
        changed = AgentProfile(
            employee_id="ENG-001", name="Backend Specialist",
            department="Engineering", role="Backend Engineer",
            capabilities=frozenset({"python", "api_design"}),
            reports_to="YUKI-CEO", permissions=frozenset(),
        )
        self.worker.registry = AgentRegistry([changed])
        result = self.worker.run_once()
        self.assertEqual(result["status"], "employee_policy_changed")
        self.assertEqual(self.engine.get_task(task["id"])["state"], "failed")
        self.assertEqual(self.executor.calls, [])

    def test_executor_denial_is_not_reported_as_success(self):
        task = self.submit()
        self.executor.outcome = Outcome(False, "approval_required")
        result = self.worker.run_once()
        self.assertEqual(result["status"], "approval_required")
        self.assertNotEqual(self.engine.get_task(task["id"])["state"], "succeeded")

    def test_rejects_payload_that_claims_preapproval(self):
        payload = {
            "description": "Review API code",
            "assigned_employee_id": self.employee.employee_id,
            "required_capabilities": ["python"],
            "required_permissions": [],
            "max_autonomy_tier": 1,
            "execution_authorized": True,
        }
        task = self.submit(payload=payload)
        result = self.worker.run_once()
        self.assertEqual(result["status"], "invalid_payload")
        self.assertEqual(self.executor.calls, [])


if __name__ == "__main__":
    unittest.main()
