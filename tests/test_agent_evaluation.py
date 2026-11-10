import tempfile
import unittest
from pathlib import Path

from automation.agent_evaluation import (
    AgentEvaluationEngine,
    EvaluationConflict,
    EvaluationError,
)


class AgentEvaluationEngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.allowed = True
        self.engine = AgentEvaluationEngine(
            str(Path(self.tmp.name) / "reviews.sqlite3"),
            registered_reviewer_ids=("security", "qa", "architecture"),
            authorize=lambda actor, action, subject: self.allowed and actor in {
                "security", "qa", "architecture", "supervisor"
            } and action in {"agent:review:submit", "agent:review:read"},
            required_reviews=2,
        )

    def submit(self, reviewer, verdict="approve", scores=None, findings=(), key=None):
        return self.engine.submit_review(
            actor_id=reviewer, reviewer_id=reviewer, subject_id="change-42",
            verdict=verdict, scores=scores or {"correctness": 5, "security": 4},
            findings=findings, idempotency_key=key or f"{reviewer}-review",
        )

    def test_waits_for_independent_quorum(self):
        self.submit("security")
        result = self.engine.consensus(actor_id="supervisor", subject_id="change-42")
        self.assertEqual(result.status, "awaiting_reviews")
        self.assertEqual(result.review_count, 1)

    def test_two_approvals_with_passing_scores_reach_consensus(self):
        self.submit("security")
        self.submit("qa")
        result = self.engine.consensus(actor_id="supervisor", subject_id="change-42")
        self.assertEqual(result.status, "approved")
        self.assertEqual(result.verdict_counts["approve"], 2)
        self.assertEqual(result.average_scores["correctness"], 5.0)

    def test_reject_is_a_veto(self):
        self.submit("security", "reject", findings=("Critical injection issue",))
        self.submit("qa")
        result = self.engine.consensus(actor_id="supervisor", subject_id="change-42")
        self.assertEqual(result.status, "rejected")
        self.assertTrue(result.unresolved_findings)

    def test_revision_vote_prevents_approval(self):
        self.submit("security")
        self.submit("qa", "revise", findings=("Missing regression test",))
        result = self.engine.consensus(actor_id="supervisor", subject_id="change-42")
        self.assertEqual(result.status, "revision_required")

    def test_low_scores_prevent_approval(self):
        self.submit("security", scores={"correctness": 2})
        self.submit("qa", scores={"correctness": 5})
        result = self.engine.consensus(actor_id="supervisor", subject_id="change-42")
        self.assertEqual(result.status, "revision_required")

    def test_same_reviewer_cannot_supply_second_independent_vote(self):
        self.submit("security")
        with self.assertRaises(EvaluationConflict):
            self.submit("security", key="second-key")

    def test_idempotency_and_conflict_detection(self):
        first = self.submit("security")
        again = self.submit("security")
        self.assertEqual(first.review_id, again.review_id)
        with self.assertRaises(EvaluationConflict):
            self.submit("security", verdict="reject", key="security-review")

    def test_spoofing_and_acl_failure_are_rejected(self):
        with self.assertRaises(PermissionError):
            self.engine.submit_review(
                actor_id="qa", reviewer_id="security", subject_id="change-42",
                verdict="approve", scores={"quality": 4}, findings=(),
                idempotency_key="spoof",
            )
        self.allowed = False
        with self.assertRaises(PermissionError):
            self.engine.consensus(actor_id="supervisor", subject_id="change-42")

    def test_invalid_scores_and_verdicts_are_rejected(self):
        with self.assertRaises(EvaluationError):
            self.submit("security", scores={"quality": 6})
        with self.assertRaises(EvaluationError):
            self.submit("security", verdict="maybe")

    def test_reviews_survive_reopening_database(self):
        self.submit("security")
        reopened = AgentEvaluationEngine(
            str(Path(self.tmp.name) / "reviews.sqlite3"),
            registered_reviewer_ids=("security", "qa"),
            authorize=lambda *_: True,
            required_reviews=2,
        )
        self.assertEqual(reopened.consensus(
            actor_id="supervisor", subject_id="change-42"
        ).review_count, 1)


if __name__ == "__main__":
    unittest.main()
