import unittest

from automation.evolution_orchestrator import (
    EvolutionExecution,
    EvolutionOrchestrationError,
    EvolutionOrchestrator,
    EvolutionPlan,
    plan_digest,
)
from automation.evolution_review_verifier import (
    CheckResult, ReviewEvidence, ReviewVerdict, VerificationReport,
)


class EvolutionOrchestratorTests(unittest.TestCase):
    def setUp(self):
        self.sha = "a" * 40
        self.actor = "human-operator"
        self.events = []
        self.calls = []
        self.changes = ({"path": "README.md", "new_sha256": "b" * 64},)
        digest = plan_digest(plan_id="plan-1", actor_id=self.actor,
                             summary="Improve documentation", changes=self.changes)
        self.plan = EvolutionPlan("plan-1", self.actor, "Improve documentation",
                                  self.changes, digest)
        self.execution = EvolutionExecution(self.sha, "exec-1", "ledger:exec-1")
        self.review = ReviewEvidence("independent-reviewer", self.actor, self.sha,
                                     ReviewVerdict.APPROVE)
        names = ("unit_tests", "integration_tests", "static_analysis",
                 "dependency_audit", "secret_scan", "security_tests")
        self.report = VerificationReport(
            self.sha, tuple(CheckResult(name, self.sha, True, "run-" + name) for name in names)
        )
        self.orchestrator = self.build()

    def build(self, **overrides):
        adapters = dict(
            planner=lambda actor, task: self.plan,
            approval_verifier=lambda plan: True,
            executor=lambda plan: self.execution,
            reviewer=lambda plan, execution: self.review,
            verification_provider=lambda plan, execution: self.report,
            ledger=self.events.append,
        )
        adapters.update(overrides)
        return EvolutionOrchestrator(**adapters)

    def test_runs_lifecycle_in_order_and_only_proposes_human_merge_review(self):
        result = self.orchestrator.run(actor_id=self.actor, task="Improve Yuki safely")
        self.assertEqual(result.status, "ready_for_human_merge_review")
        self.assertTrue(result.decision.allowed_to_propose_merge)
        self.assertEqual(
            [event["stage"] for event in self.events],
            ["started", "planned", "approved", "executed", "verified"],
        )

    def test_denied_approval_never_executes_or_reviews(self):
        calls = []
        orchestrator = self.build(
            approval_verifier=lambda plan: False,
            executor=lambda plan: calls.append("execute"),
            reviewer=lambda plan, execution: calls.append("review"),
        )
        with self.assertRaises(EvolutionOrchestrationError):
            orchestrator.run(actor_id=self.actor, task="No unauthorized execution")
        self.assertEqual(calls, [])
        self.assertEqual(self.events[-1]["stage"], "approval_denied")

    def test_non_boolean_approval_fails_closed(self):
        for value in (None, 1, "approved"):
            self.events.clear()
            orchestrator = self.build(approval_verifier=lambda plan, value=value: value)
            with self.assertRaises(EvolutionOrchestrationError):
                orchestrator.run(actor_id=self.actor, task="Check approval")
        self.assertNotIn("executed", [event["stage"] for event in self.events])

    def test_plan_mutation_during_approval_is_detected_before_execution(self):
        calls = []
        def mutate(plan):
            plan.changes[0]["new_sha256"] = "c" * 64
            return True
        orchestrator = self.build(
            approval_verifier=mutate,
            executor=lambda plan: calls.append("execute"),
        )
        with self.assertRaisesRegex(EvolutionOrchestrationError, "changed after approval"):
            orchestrator.run(actor_id=self.actor, task="Mutation attempt")
        self.assertEqual(calls, [])

    def test_approval_backend_exception_fails_closed(self):
        orchestrator = self.build(approval_verifier=lambda plan: 1 / 0)
        with self.assertRaisesRegex(EvolutionOrchestrationError, "failed closed"):
            orchestrator.run(actor_id=self.actor, task="Check backend")
        self.assertNotIn("executed", [event["stage"] for event in self.events])

    def test_bad_plan_digest_stops_before_approval(self):
        bad = EvolutionPlan("plan-1", self.actor, "Altered",
                            self.changes, self.plan.plan_digest)
        calls = []
        orchestrator = self.build(planner=lambda actor, task: bad,
                                  approval_verifier=lambda plan: calls.append("approve"))
        with self.assertRaises(EvolutionOrchestrationError):
            orchestrator.run(actor_id=self.actor, task="Bad plan")
        self.assertEqual(calls, [])

    def test_plan_actor_must_match_requester(self):
        bad = EvolutionPlan("plan-1", "different-actor", "Improve documentation",
                            self.changes, "a" * 64)
        orchestrator = self.build(planner=lambda actor, task: bad)
        with self.assertRaises(EvolutionOrchestrationError):
            orchestrator.run(actor_id=self.actor, task="Actor mismatch")

    def test_invalid_execution_evidence_blocks_review(self):
        calls = []
        bad = EvolutionExecution("not-a-commit", "exec-1", "ledger:exec")
        orchestrator = self.build(executor=lambda plan: bad,
                                  reviewer=lambda *args: calls.append("review"))
        with self.assertRaises(EvolutionOrchestrationError):
            orchestrator.run(actor_id=self.actor, task="Bad execution")
        self.assertEqual(calls, [])

    def test_review_rejection_blocks_merge_proposal(self):
        rejected = ReviewEvidence("independent-reviewer", self.actor, self.sha,
                                  ReviewVerdict.REQUEST_CHANGES, ("Fix issue",))
        orchestrator = self.build(reviewer=lambda *args: rejected)
        result = orchestrator.run(actor_id=self.actor, task="Review rejection")
        self.assertEqual(result.status, "blocked")
        self.assertFalse(result.decision.allowed_to_propose_merge)

    def test_ledger_failure_stops_before_execution(self):
        calls = []
        def ledger(event):
            if event["stage"] == "planned":
                raise OSError("ledger unavailable")
        orchestrator = self.build(ledger=ledger,
                                  executor=lambda plan: calls.append("execute"))
        with self.assertRaisesRegex(EvolutionOrchestrationError, "ledger unavailable"):
            orchestrator.run(actor_id=self.actor, task="Ledger failure")
        self.assertEqual(calls, [])

    def test_invalid_actor_and_task_are_rejected(self):
        with self.assertRaises(ValueError):
            self.orchestrator.run(actor_id="../actor", task="Task")
        with self.assertRaises(ValueError):
            self.orchestrator.run(actor_id=self.actor, task=" ")

    def test_planner_exception_does_not_continue(self):
        calls = []
        orchestrator = self.build(planner=lambda *args: (_ for _ in ()).throw(RuntimeError()),
                                  approval_verifier=lambda plan: calls.append("approve"))
        with self.assertRaises(EvolutionOrchestrationError):
            orchestrator.run(actor_id=self.actor, task="Planner error")
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
