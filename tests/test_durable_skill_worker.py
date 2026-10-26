"""Integration tests for the durable-task to approved-skill bridge."""
from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from core.approved_skill_runtime import ApprovedSkillRuntime
from core.approved_skill_wiring import SkillApprovalStore
from core.durable_skill_worker import DurableSkillWorker
from core.durable_task_engine import DurableTaskEngine
from core.guarded_skill_runtime import OneShotSkillApprovals


class Registry:
    def __init__(self, *skills):
        self.skills = {skill.name: skill for skill in skills}

    def get(self, name):
        return self.skills.get(name)


class ReadSkill:
    name = "read_demo"
    risk = "low"
    requires_approval = False

    def execute(self, argument=""):
        return {"ok": True, "message": "done:" + argument, "secret_output": "do-not-persist"}


class HighRiskSkill:
    name = "write_demo"
    risk = "high"
    requires_approval = True

    def __init__(self):
        self.calls = 0

    def execute(self, argument=""):
        self.calls += 1
        return {"ok": True, "message": "written"}


class DurableSkillWorkerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.engine = DurableTaskEngine(root / "tasks.sqlite")
        self.addCleanup(self.engine.close)
        self.approvals = OneShotSkillApprovals()
        self.high_risk = HighRiskSkill()
        self.registry = Registry(ReadSkill(), self.high_risk)
        self.runtime = ApprovedSkillRuntime(
            self.registry,
            SkillApprovalStore(root / "approvals.sqlite"),
            invocation_approvals=self.approvals,
        )
        self.worker = DurableSkillWorker(self.engine, self.runtime, worker_id="test-worker")
        self.goal = self.engine.create_goal()

    def submit(self, skill_name, argument="", *, task_type="skill.invoke", max_attempts=1, key="task"):
        return self.engine.submit(
            self.goal,
            task_type,
            {"skill_name": skill_name, "argument": argument},
            idempotency_key=key,
            max_attempts=max_attempts,
        )

    def test_registered_low_risk_skill_runs_and_only_metadata_is_persisted(self):
        task = self.submit("read_demo", "hello")
        result = self.worker.run_once()
        self.assertEqual(result["task_id"], task["id"])
        self.assertEqual(result["state"], "succeeded")
        self.assertEqual(result["message"], "done:hello")
        stored = self.engine.get_task(task["id"])
        self.assertNotIn("secret_output", str(stored["result"]))
        self.assertNotIn("do-not-persist", str(stored["result"]))

    def test_high_risk_skill_requires_fresh_approval_before_execution(self):
        task = self.submit("write_demo", "change")
        result = self.worker.run_once()
        self.assertEqual(result["status"], "approval_required")
        self.assertEqual(result["state"], "failed")
        self.assertEqual(self.high_risk.calls, 0)

    def test_high_risk_skill_executes_after_exact_argument_approval(self):
        task = self.submit("write_demo", "change", key="approved-task")
        self.approvals.approve("write_demo", "change")
        result = self.worker.run_once()
        self.assertEqual(result["state"], "succeeded")
        self.assertEqual(self.high_risk.calls, 1)
        self.assertEqual(self.engine.get_task(task["id"])["result"]["skill_name"], "write_demo")

    def test_unsupported_task_type_is_terminally_rejected_without_execution(self):
        task = self.submit(
            "read_demo", task_type="arbitrary.python",
            max_attempts=5, key="unsupported"
        )
        result = self.worker.run_once()
        self.assertEqual(result["status"], "unsupported_task")
        stored = self.engine.get_task(task["id"])
        self.assertEqual(stored["state"], "failed")
        self.assertEqual(stored["attempts"], 1)
        self.assertIn("not supported", stored["last_error"])

    def test_invalid_payload_is_rejected_without_skill_execution(self):
        task = self.engine.submit(
            self.goal,
            "skill.invoke",
            {"skill_name": "read_demo", "extra": "not allowed"},
            idempotency_key="invalid-payload",
            max_attempts=1,
        )
        result = self.worker.run_once()
        self.assertEqual(result["status"], "invalid_payload")
        self.assertEqual(self.engine.get_task(task["id"])["state"], "failed")

    def test_worker_exception_marks_single_attempt_task_failed(self):
        class RaisingRuntime:
            def invoke(self, _skill_name, _argument):
                raise RuntimeError("secret token should not be persisted")

        task = self.submit("read_demo", "raises", key="worker-exception")
        worker = DurableSkillWorker(
            self.engine, RaisingRuntime(), worker_id="raising-worker"
        )
        result = worker.run_once()
        self.assertEqual(result["state"], "failed")
        stored = self.engine.get_task(task["id"])
        self.assertEqual(stored["state"], "failed")
        self.assertNotIn("secret token", str(stored["last_error"]))
        self.assertEqual(stored["attempts"], 1)

    def test_worker_reports_committed_success_if_followup_response_fails(self):
        task = self.submit("read_demo", "commit-then-error", key="commit-response-error")
        original_complete = self.engine.complete

        def commit_then_raise(task_id, worker_id, result=None):
            original_complete(task_id, worker_id, result)
            raise RuntimeError("simulated lost response")

        self.engine.complete = commit_then_raise
        result = self.worker.run_once()
        self.assertEqual(result["task_id"], task["id"])
        self.assertEqual(result["state"], "succeeded")
        self.assertEqual(self.engine.get_task(task["id"])["state"], "succeeded")

    def test_worker_never_claims_unregistered_skill_as_success(self):
        task = self.submit("not_registered", key="missing-skill")
        result = self.worker.run_once()
        self.assertFalse(result["state"] == "succeeded")
        self.assertEqual(self.engine.get_task(task["id"])["state"], "failed")


if __name__ == "__main__":
    unittest.main()
