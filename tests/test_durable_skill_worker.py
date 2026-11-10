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
        from core.tool_policy import Decision, PolicyResult
        class AllowPolicy:
            def evaluate(self, request):
                return PolicyResult(Decision.ALLOW, "test-v1", ("test-owner-allow",), "test")
        self.runtime = ApprovedSkillRuntime(
            self.registry,
            SkillApprovalStore(root / "approvals.sqlite"),
            invocation_approvals=self.approvals,
            policy_engine=AllowPolicy(),
            policy_request_factory=lambda name, argument, skill, context: context,
        )
        self.worker = DurableSkillWorker(self.engine, self.runtime, worker_id="test-worker", owner_id="test-owner")
        self.goal = self.engine.create_goal(owner_id="test-owner")

    def submit(self, skill_name, argument="", *, task_type="skill.invoke", max_attempts=1, key="task"):
        return self.engine.submit(
            self.goal,
            task_type,
            {"skill_name": skill_name, "argument": argument},
            idempotency_key=key,
            max_attempts=max_attempts,
            owner_id="test-owner",
        )

    def test_gateway_approval_is_resolved_outside_task_payload_and_passed_to_runtime(self):
        from core.capability_approval_gateway import CapabilityApprovalGateway
        from core.guarded_skill_runtime import SkillPolicyContext
        from core.tool_policy import (
            Decision, PolicyRequest, PolicyRule, Risk, ToolPolicyEngine, argument_digest,
        )

        engine = ToolPolicyEngine((
            PolicyRule(
                "durable-read-approval", Decision.REQUIRE_APPROVAL,
                capability="skill.invoke", principal_id="test-owner",
                trigger="durable_task", target="read_demo", risk=Risk.LOW,
            ),
        ), policy_version="durable-approval-v1")
        gateway = CapabilityApprovalGateway(
            Path(self.temp.name) / "gateway.sqlite", engine,
            authorize_approver=lambda actor, request: actor == "admin",
        )
        runtime = ApprovedSkillRuntime(
            self.registry,
            SkillApprovalStore(Path(self.temp.name) / "gateway-skills.sqlite"),
            policy_engine=engine,
            policy_request_factory=lambda name, argument, skill, context: PolicyRequest(
                principal_id=context.principal_id,
                trigger=context.trigger,
                capability="skill.invoke",
                target=name,
                argument_sha256=argument_digest({"argument": argument}),
                risk=Risk.LOW,
                run_id=context.goal_id,
                step_id=context.task_id,
            ),
            approval_gateway=gateway,
        )
        with self.assertRaisesRegex(ValueError, "approval_token_resolver"):
            DurableSkillWorker(
                self.engine, runtime, worker_id="missing-resolver",
                owner_id="test-owner",
            )

        task = self.submit("read_demo", "gateway-approved", key="gateway-approved")
        context = SkillPolicyContext(
            principal_id="test-owner", trigger="durable_task",
            goal_id=self.goal, task_id=task["id"],
        )
        request = PolicyRequest(
            principal_id="test-owner", trigger="durable_task",
            capability="skill.invoke", target="read_demo",
            argument_sha256=argument_digest({"argument": "gateway-approved"}),
            risk=Risk.LOW, run_id=self.goal, step_id=task["id"],
        )
        token = gateway.issue_approval(request, approved_by="admin")
        resolver_calls = []

        def resolve_approval(policy_context, skill_name):
            resolver_calls.append((policy_context, skill_name))
            return token

        result = DurableSkillWorker(
            self.engine, runtime, worker_id="gateway-worker",
            owner_id="test-owner", approval_token_resolver=resolve_approval,
        ).run_once()
        self.assertEqual(result["state"], "succeeded")
        self.assertEqual(result["status"], "completed")
        self.assertEqual(len(resolver_calls), 1)
        self.assertEqual(resolver_calls[0][0], context)
        self.assertEqual(resolver_calls[0][1], "read_demo")
        stored = self.engine.get_task(task["id"])
        self.assertNotIn(token, str(stored))
        self.assertNotIn("approval_token", str(stored["payload"]))

    def test_worker_requires_central_policy_by_default(self):
        runtime = ApprovedSkillRuntime(
            self.registry,
            SkillApprovalStore(Path(self.temp.name) / "no-policy.sqlite"),
        )
        with self.assertRaisesRegex(ValueError, "central policy engine"):
            DurableSkillWorker(self.engine, runtime, worker_id="unsafe-worker")

    def test_policy_context_is_derived_from_persisted_authenticated_task(self):
        observed = []
        from core.tool_policy import Decision, PolicyResult
        class CapturePolicy:
            def evaluate(self, request):
                observed.append(request)
                return PolicyResult(Decision.ALLOW, "capture-v1", ("capture",), "test")
        runtime = ApprovedSkillRuntime(
            self.registry,
            SkillApprovalStore(Path(self.temp.name) / "capture.sqlite"),
            policy_engine=CapturePolicy(),
            policy_request_factory=lambda name, argument, skill, context: context,
        )
        task = self.submit("read_demo", "provenance", key="provenance-task")
        result = DurableSkillWorker(
            self.engine, runtime, worker_id="capture-worker", owner_id="test-owner"
        ).run_once()
        self.assertEqual(result["state"], "succeeded")
        self.assertEqual(observed[0].principal_id, "test-owner")
        self.assertEqual(observed[0].trigger, "durable_task")
        self.assertEqual(observed[0].goal_id, self.goal)
        self.assertEqual(observed[0].task_id, task["id"])

    def test_policy_worker_rejects_ownerless_legacy_task_before_invocation(self):
        from core.tool_policy import Decision, PolicyResult
        class AllowPolicy:
            def evaluate(self, request):
                return PolicyResult(Decision.ALLOW, "test-v1", ("allow",), "test")
        calls = []
        class Runtime:
            policy_engine = AllowPolicy()
            def invoke(self, *args, **kwargs):
                calls.append((args, kwargs))
                raise AssertionError("ownerless task must not invoke")
        legacy_goal = self.engine.create_goal()
        legacy_task = self.engine.submit(
            legacy_goal, "skill.invoke",
            {"skill_name": "read_demo", "argument": "legacy"},
            idempotency_key="ownerless-legacy", max_attempts=1,
        )
        result = DurableSkillWorker(
            self.engine, Runtime(), worker_id="policy-worker"
        ).run_once()
        self.assertEqual(result["status"], "missing_authenticated_owner")
        self.assertEqual(result["state"], "failed")
        self.assertEqual(self.engine.get_task(legacy_task["id"])["state"], "failed")
        self.assertEqual(calls, [])

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

    def test_high_risk_approval_cannot_be_reused_by_another_principal(self):
        self.approvals.approve("write_demo", "change", principal_id="alice")
        task = self.engine.submit(
            self.goal, "skill.invoke",
            {"skill_name": "write_demo", "argument": "change"},
            idempotency_key="bob-cannot-reuse-alice-approval",
            owner_id="test-owner", max_attempts=1,
        )
        result = self.worker.run_once()
        self.assertEqual(result["status"], "approval_required")
        self.assertEqual(result["state"], "failed")
        self.assertEqual(self.high_risk.calls, 0)

    def test_high_risk_skill_executes_after_exact_argument_approval(self):
        task = self.submit("write_demo", "change", key="approved-task")
        self.approvals.approve("write_demo", "change", principal_id="test-owner")
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
            owner_id="test-owner",
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
            self.engine, RaisingRuntime(), worker_id="raising-worker", require_policy=False
        )
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
            self.engine,
            SlowRuntime(),
            worker_id="heartbeat-worker",
            lease_seconds=2,
            heartbeat_interval_seconds=0.05,
            require_policy=False,
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


if __name__ == "__main__":
    unittest.main()
