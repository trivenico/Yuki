import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from evaluation.evolution_candidates import EvolutionCandidateRegistry


class EvolutionCandidateRegistryTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database = Path(self.temp_dir.name) / "candidates.sqlite3"
        self.registry = EvolutionCandidateRegistry(self.database)
        self.candidate = self.registry.propose(
            owner_id="owner-a", kind="workflow",
            title="Add a preflight check",
            rationale="A recurring failure suggests a missing validation step.",
            expected_outcome="Reduce avoidable task failures.",
            evidence_refs=("observation:abc123",),
            risk_tags=("workflow-change", "requires-review"),
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_candidate_is_durable_and_digest_bound_to_content(self):
        reopened = EvolutionCandidateRegistry(self.database)
        loaded = reopened.get(owner_id="owner-a", candidate_id=self.candidate.candidate_id)
        self.assertEqual(loaded, self.candidate)
        self.assertEqual(len(loaded.payload_digest), 64)

    def test_owner_scope_prevents_cross_owner_reads(self):
        with self.assertRaises(KeyError):
            self.registry.get(owner_id="owner-b", candidate_id=self.candidate.candidate_id)

    def test_review_is_single_step_and_bound_to_digest_and_revision(self):
        reviewed = self.registry.review(
            owner_id="owner-a", candidate_id=self.candidate.candidate_id,
            expected_digest=self.candidate.payload_digest, expected_revision=0,
            reviewer_id="owner-review", decision="accepted_for_evaluation",
            note="Proceed to a separately controlled evaluation stage.",
        )
        self.assertEqual(reviewed.state, "accepted_for_evaluation")
        self.assertEqual(reviewed.revision, 1)
        with self.assertRaises(ValueError):
            self.registry.review(
                owner_id="owner-a", candidate_id=self.candidate.candidate_id,
                expected_digest=self.candidate.payload_digest, expected_revision=0,
                reviewer_id="owner-review", decision="rejected",
            )

    def test_needs_evidence_candidate_can_be_resubmitted_for_review(self):
        deferred = self.registry.review(
            owner_id="owner-a", candidate_id=self.candidate.candidate_id,
            expected_digest=self.candidate.payload_digest, expected_revision=0,
            reviewer_id="owner-review", decision="needs_evidence",
            note="Add a verified task outcome reference.",
        )
        self.assertEqual(deferred.state, "needs_evidence")
        accepted = self.registry.review(
            owner_id="owner-a", candidate_id=self.candidate.candidate_id,
            expected_digest=self.candidate.payload_digest, expected_revision=1,
            reviewer_id="owner-review", decision="accepted_for_evaluation",
        )
        self.assertEqual(accepted.state, "accepted_for_evaluation")
        self.assertEqual(accepted.revision, 2)

    def test_stale_digest_or_revision_cannot_record_review(self):
        with self.assertRaises(ValueError):
            self.registry.review(
                owner_id="owner-a", candidate_id=self.candidate.candidate_id,
                expected_digest="0" * 64, expected_revision=0,
                reviewer_id="owner-review", decision="rejected",
            )
        with self.assertRaises(ValueError):
            self.registry.review(
                owner_id="owner-a", candidate_id=self.candidate.candidate_id,
                expected_digest=self.candidate.payload_digest, expected_revision=1,
                reviewer_id="owner-review", decision="rejected",
            )

    def test_rejects_unbounded_or_unsupported_candidate_fields(self):
        with self.assertRaises(ValueError):
            self.registry.propose(
                owner_id="owner-a", kind="tool-execution",
                title="Run arbitrary code", rationale="No", expected_outcome="No",
            )
        with self.assertRaises(ValueError):
            self.registry.propose(
                owner_id="owner-a", kind="prompt", title="x", rationale="y",
                expected_outcome="z", evidence_refs=["same", "same"],
            )
        with self.assertRaises(ValueError):
            self.registry.propose(
                owner_id="owner-a", kind="prompt", title="x", rationale="y",
                expected_outcome="z", parent_digest="not-a-digest",
            )

    def test_duplicate_candidate_id_is_rejected(self):
        with self.assertRaises(ValueError):
            self.registry.propose(
                owner_id="owner-a", kind="workflow", title="Different",
                rationale="Different rationale.", expected_outcome="Different outcome.",
                candidate_id=self.candidate.candidate_id,
            )

    def test_database_payload_tampering_fails_closed(self):
        with closing(sqlite3.connect(self.database)) as db:
            db.execute(
                "UPDATE evolution_candidates SET payload_json=? WHERE candidate_id=?",
                ('{"kind":"prompt"}', self.candidate.candidate_id),
            )
            db.commit()
        with self.assertRaisesRegex(RuntimeError, "integrity"):
            self.registry.get(owner_id="owner-a", candidate_id=self.candidate.candidate_id)

    def test_review_history_tampering_fails_closed(self):
        self.registry.review(
            owner_id="owner-a", candidate_id=self.candidate.candidate_id,
            expected_digest=self.candidate.payload_digest, expected_revision=0,
            reviewer_id="owner-review", decision="rejected",
        )
        with closing(sqlite3.connect(self.database)) as db:
            db.execute(
                "UPDATE evolution_candidate_events SET payload_digest=? WHERE candidate_id=?",
                ("f" * 64, self.candidate.candidate_id),
            )
            db.commit()
        with self.assertRaisesRegex(RuntimeError, "history"):
            self.registry.get(owner_id="owner-a", candidate_id=self.candidate.candidate_id)

    def test_capacity_is_enforced(self):
        small = EvolutionCandidateRegistry(Path(self.temp_dir.name) / "small.sqlite3", max_candidates=1)
        small.propose(
            owner_id="owner-a", kind="safety", title="Review boundary",
            rationale="A bounded rationale.", expected_outcome="Improve review quality.",
        )
        with self.assertRaises(ValueError):
            small.propose(
                owner_id="owner-a", kind="safety", title="Second",
                rationale="A bounded rationale.", expected_outcome="Improve review quality.",
            )


if __name__ == "__main__":
    unittest.main()
