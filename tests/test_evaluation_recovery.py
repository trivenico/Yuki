import tempfile
import unittest
from pathlib import Path

from evaluation.evolution_candidates import EvolutionCandidateRegistry
from evaluation.evaluation_recovery import EvaluationRecoverySupervisor
from evaluation.model_eval import EvalCase, ModelEvaluator
from evaluation.run_store import EvaluationRunStore


class EvaluationRecoverySupervisorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.runs = EvaluationRunStore(root / "runs.sqlite3")
        self.registry = EvolutionCandidateRegistry(root / "candidates.sqlite3")
        self.supervisor = EvaluationRecoverySupervisor(root / "recovery.sqlite3")
        self.candidate = self.registry.propose(
            owner_id="owner-a", kind="prompt", title="Candidate",
            rationale="Recovery test", expected_outcome="Link is restored",
            candidate_id="candidate-recovery",
        )
        self.registry.review(
            owner_id="owner-a", candidate_id=self.candidate.candidate_id,
            expected_digest=self.candidate.payload_digest, expected_revision=0,
            reviewer_id="owner-reviewer", decision="accepted_for_evaluation",
        )
        report = ModelEvaluator().run(
            self.candidate.candidate_id, lambda prompt: "benchmark completed",
            [EvalCase("case", "reasoning", "Run benchmark", required_terms=("benchmark",))],
        )
        self.run = self.runs.record(
            owner_id="owner-a", idempotency_key="recovery-run",
            benchmark_id="benchmark-a", benchmark_version=1,
            benchmark_digest="b" * 64, candidate_id=self.candidate.candidate_id,
            provider_id="local", model_id="test-model", environment={},
            report=report, candidate_digest=self.candidate.payload_digest,
        )

    def test_plan_is_read_only_and_apply_repairs_orphan(self):
        plan = self.supervisor.plan(
            owner_id="owner-a", run_store=self.runs, registry=self.registry
        )
        self.assertEqual([item.status for item in plan.items], ["planned"])
        self.assertEqual(self.registry.list_evaluations(
            owner_id="owner-a", candidate_id=self.candidate.candidate_id,
            run_store=self.runs,
        ), ())
        results = self.supervisor.apply(
            plan=plan, run_store=self.runs, registry=self.registry
        )
        self.assertEqual(results[0].status, "repaired")
        self.assertEqual(self.supervisor.list_events(owner_id="owner-a")[-1]["outcome"], "repaired")

    def test_plan_rejects_candidate_no_longer_accepted(self):
        self.registry.review(
            owner_id="owner-a", candidate_id=self.candidate.candidate_id,
            expected_digest=self.candidate.payload_digest, expected_revision=1,
            reviewer_id="owner-reviewer", decision="rejected",
        )
        plan = self.supervisor.plan(
            owner_id="owner-a", run_store=self.runs, registry=self.registry
        )
        self.assertEqual(plan.items[0].status, "rejected")
        self.assertIn("not accepted", plan.items[0].detail)

    def test_audit_events_are_immutable(self):
        self.supervisor.plan(
            owner_id="owner-a", run_store=self.runs, registry=self.registry
        )
        import sqlite3
        with sqlite3.connect(self.supervisor.database_path) as db:
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("UPDATE evaluation_recovery_events SET outcome='repaired'")
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("DELETE FROM evaluation_recovery_events")


if __name__ == "__main__":
    unittest.main()
