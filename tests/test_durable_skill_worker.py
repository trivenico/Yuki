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
from core.authorization import AuthorizationDecision, Principal
from core.authorization_gateway import AuthorizationGateway


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
        self.policy = self._policy()
        self.gateway = AuthorizationGateway(self.policy, audit=lambda *_: None)
        self.worker = self.make_worker("test-worker")
        self.goal = self.engine.create_goal()

    @staticmethod
    def _policy(allowed=True):
        class Policy:
            def __init__(self, decision):
                self.allowed = decision
                self.requests = []
            def authorize(self, request):
                self.requests.append(request)
                return AuthorizationDecision(self.allowed, "decision-1", "policy-v1", "allowed" if self.allowed else "denied")
        return Policy(allowed)

    def make_worker(self, worker_id, runtime=None, gateway=None, principal=None):
        return DurableSkillWorker(
            self.engine, runtime if runtime is not None else self.runtime,
            worker_id=worker_id,
            authorization_gateway=gateway if gateway is not None else self.gateway,
            principal_for_worker=lambda _worker: principal or Principal(
                "workload:test-worker", "workload", tenant_id="tenant-1", agent_id="agent-1"
            ),
            policy_revision="policy-v1",
        )

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
        worker = self.make_worker("raising-worker", runtime=RaisingRuntime())
        result = worker.run_once()
        self.assertEqual(result["state"], "failed")
        stored = self.engine.get_task(task["id"])
        self.assertEqual(stored["state"], "failed")
        self.assertNotIn("secret token", str(stored["last_error"]))
        self.assertEqual(stored["attempts"], 1)

    def test_worker_rejects_invalid_heartbeat_intervals(self):
        for interval in (True, 0, -1, float("nan"), float("inf"), 2.1, 301):
            with self.subTest(interval=interval):
                with self.assertRaises(ValueError):
                    DurableSkillWorker(
                        self.engine,
                        self.runtime,
                        worker_id="invalid-heartbeat",
                        lease_seconds=4,
                        heartbeat_interval_seconds=interval,
                        authorization_gateway=self.gateway,
                        principal_for_worker=lambda _worker: Principal(
                            "workload:invalid-heartbeat",
                            "workload",
                            tenant_id="tenant-1",
                            agent_id="agent-1",
                        ),
                        policy_revision="policy-v1",
                    )

    def test_worker_renews_lease_during_long_running_invocation(self):
        import threading

        started = threading.Event()
        release = threading.Event()

        class SlowRuntime:
            def invoke(self, _skill_name, _argument):
                started.set()
                if not release.wait(timeout=8):
                    raise RuntimeError("test invocation timed out")
                return type("Result", (), {
                    "ok": True, "status": "completed", "message": "done"
                })()

        task = self.submit("read_demo", "slow", key="lease-heartbeat")
        worker = DurableSkillWorker(
            self.engine, SlowRuntime(), worker_id="heartbeat-worker",
            lease_seconds=2, heartbeat_interval_seconds=0.05,
            authorization_gateway=self.gateway,
            principal_for_worker=lambda _worker: Principal(
                "workload:heartbeat", "workload", tenant_id="tenant-1", agent_id="agent-1"
            ),
            policy_revision="policy-v1",
        )
        outcomes = []
        thread = threading.Thread(target=lambda: outcomes.append(worker.run_once()))
        thread.start()
        self.assertTrue(started.wait(timeout=2))
        try:
            # Wait longer than the original lease; heartbeats must keep the
            # task owned and prevent another recovery/claim from stealing it.
            import time
            time.sleep(2.2)
            self.assertEqual(self.engine.recover_expired_leases(), 0)
            self.assertEqual(self.engine.get_task(task["id"])["state"], "running")
        finally:
            release.set()
        thread.join(timeout=5)
        self.assertFalse(thread.is_alive(), "worker did not finish after invocation release")
        self.assertEqual(outcomes[0]["state"], "succeeded")
        self.assertEqual(self.engine.get_task(task["id"])["state"], "succeeded")

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


    def test_central_authorization_binds_workload_and_hashed_skill_intent(self):
        task = self.submit("read_demo", "private argument", key="gateway-bound")
        result = self.worker.run_once()
        self.assertEqual(result["state"], "succeeded")
        request = self.policy.requests[0]
        self.assertEqual(request.action, "skill:invoke")
        self.assertEqual(request.risk, "high")
        self.assertEqual(request.task_id, task["id"])
        self.assertEqual(request.principal.kind, "workload")
        self.assertTrue(request.resource.startswith("skill-invocation:sha256:"))
        self.assertNotIn("private argument", request.resource)
        self.assertEqual(len(request.resource), len("skill-invocation:sha256:") + 64)

    def test_central_denial_prevents_skill_execution(self):
        task = self.submit("read_demo", "blocked", key="gateway-deny")
        deny_gateway = AuthorizationGateway(self._policy(False), audit=lambda *_: None)
        worker = self.make_worker("test-worker", gateway=deny_gateway)
        result = worker.run_once()
        self.assertEqual(result["state"], "failed")
        self.assertEqual(self.engine.get_task(task["id"])["state"], "failed")

    def test_audit_failure_prevents_skill_execution(self):
        task = self.submit("read_demo", "blocked", key="gateway-audit-fail")
        broken_gateway = AuthorizationGateway(self._policy(True), audit=lambda *_: False)
        worker = self.make_worker("test-worker", gateway=broken_gateway)
        result = worker.run_once()
        self.assertEqual(result["state"], "failed")
        self.assertEqual(self.engine.get_task(task["id"])["state"], "failed")

    def test_untrusted_or_non_workload_identity_prevents_invocation(self):
        task = self.submit("read_demo", "blocked", key="invalid-workload-principal")
        invalid = Principal("service:test-worker", "service", tenant_id="tenant-1")
        worker = self.make_worker("test-worker", principal=invalid)
        with self.assertRaises(PermissionError):
            worker.run_once()
        stored = self.engine.get_task(task["id"])
        self.assertEqual(stored["state"], "queued")
        self.assertEqual(stored["attempts"], 0)

    def test_identity_resolver_failure_does_not_claim_task(self):
        task = self.submit("read_demo", "blocked", key="resolver-failure")
        worker = DurableSkillWorker(
            self.engine, self.runtime, worker_id="untrusted-worker",
            authorization_gateway=self.gateway,
            principal_for_worker=lambda _worker: (_ for _ in ()).throw(RuntimeError("identity unavailable")),
            policy_revision="policy-v1",
        )
        with self.assertRaises(RuntimeError):
            worker.run_once()
        stored = self.engine.get_task(task["id"])
        self.assertEqual(stored["state"], "queued")
        self.assertEqual(stored["attempts"], 0)

    def test_worker_requires_gateway_and_authenticated_identity_resolver(self):
        with self.assertRaises(TypeError):
            DurableSkillWorker(self.engine, self.runtime, worker_id="missing-gateway")
        with self.assertRaises(TypeError):
            DurableSkillWorker(self.engine, self.runtime, worker_id="missing-resolver", authorization_gateway=self.gateway)


if __name__ == "__main__":
    unittest.main()
