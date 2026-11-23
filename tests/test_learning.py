import tempfile
import sqlite3
from datetime import datetime, timedelta, timezone
import unittest
from pathlib import Path

from companion.memory import MemoryStore
from core.learning import LearningCoordinator
from core.learning_store import LearningQueueStore


class Consent:
    def __init__(self, enabled=True):
        self.learning_enabled = enabled


class LearningCoordinatorTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.consents = {"alice": Consent(True), "bob": Consent(True)}
        self.memories = {
            owner: MemoryStore(Path(self.tempdir.name) / f"{owner}.json")
            for owner in ("alice", "bob")
        }
        self.learning = LearningCoordinator(
            consent_for_owner=lambda owner: self.consents[owner],
            memory_for_owner=lambda owner: self.memories[owner],
        )

    def tearDown(self):
        self.tempdir.cleanup()

    def test_foreground_feedback_is_only_saved_after_explicit_review(self):
        candidate = self.learning.propose_foreground_feedback(
            "alice",
            lesson="Prefer small, reversible migrations.",
            evidence="The user explicitly corrected the migration plan.",
        )
        self.assertEqual(self.memories["alice"].list(), [])
        self.assertTrue(self.learning.review("alice", candidate.candidate_id, approved=True))
        saved = self.memories["alice"].list()
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0]["text"], "Prefer small, reversible migrations.")

    def test_verified_background_lesson_requires_review(self):
        candidate = self.learning.propose_background_outcome(
            "alice",
            lesson="Run the focused regression suite after a lease change.",
            evidence="Worker task completed and verification reported success.",
            verified=True,
        )
        self.assertEqual(candidate.source, "background_task")
        self.assertEqual(self.memories["alice"].list(), [])
        self.assertTrue(self.learning.review("alice", candidate.candidate_id, approved=False))
        self.assertEqual(self.memories["alice"].list(), [])

    def test_unverified_background_outcome_is_rejected(self):
        with self.assertRaises(ValueError):
            self.learning.propose_background_outcome(
                "alice", lesson="Unverified", evidence="Task failed", verified=False,
            )

    def test_learning_requires_consent_and_rechecks_it_before_commit(self):
        self.consents["alice"].learning_enabled = False
        with self.assertRaises(PermissionError):
            self.learning.propose_foreground_feedback(
                "alice", lesson="Lesson", evidence="Explicit feedback",
            )
        self.consents["alice"].learning_enabled = True
        candidate = self.learning.propose_foreground_feedback(
            "alice", lesson="Lesson", evidence="Explicit feedback",
        )
        self.consents["alice"].learning_enabled = False
        with self.assertRaises(PermissionError):
            self.learning.review("alice", candidate.candidate_id, approved=True)
        self.assertEqual(self.memories["alice"].list(), [])

    def test_candidates_are_owner_isolated(self):
        candidate = self.learning.propose_foreground_feedback(
            "alice", lesson="Private lesson", evidence="Private evidence",
        )
        self.assertEqual(self.learning.pending("bob"), [])
        self.assertFalse(self.learning.review("bob", candidate.candidate_id, approved=True))
        self.assertEqual(self.memories["bob"].list(), [])
        self.assertEqual(len(self.learning.pending("alice")), 1)


class DurableLearningQueueTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tempdir.name) / "learning.sqlite3"
        self.memory = MemoryStore(Path(self.tempdir.name) / "alice-memory.json")
        self.consent = Consent(True)

    def tearDown(self):
        self.tempdir.cleanup()

    def coordinator(self):
        return LearningCoordinator(
            consent_for_owner=lambda owner: self.consent if owner == "alice" else Consent(False),
            memory_for_owner=lambda owner: self.memory if owner == "alice" else MemoryStore(
                Path(self.tempdir.name) / "other-memory.json"
            ),
            queue_store=LearningQueueStore(self.db_path),
        )

    def test_candidate_survives_coordinator_restart_and_is_owner_scoped(self):
        first = self.coordinator()
        candidate = first.propose_foreground_feedback(
            "alice", lesson="Keep database changes reversible.",
            evidence="Explicit user correction.",
        )
        restarted = self.coordinator()
        self.assertEqual(restarted.pending("alice"), [candidate])
        self.assertEqual(restarted.pending("bob"), [])
        self.assertFalse(restarted.review("bob", candidate.candidate_id, approved=True))
        self.assertEqual(self.memory.list(), [])

    def test_durable_approval_is_idempotent_and_persists_review_state(self):
        first = self.coordinator()
        candidate = first.propose_background_outcome(
            "alice", lesson="Verify worker lease ownership before completion.",
            evidence="Background task completed with successful verification.",
            verified=True,
        )
        restarted = self.coordinator()
        self.assertTrue(restarted.review("alice", candidate.candidate_id, approved=True))
        self.assertFalse(self.coordinator().review("alice", candidate.candidate_id, approved=True))
        saved = self.memory.list()
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0]["source_id"], candidate.candidate_id)
        self.assertEqual(saved[0]["category"], "lesson")

    def test_stale_review_claim_can_be_recovered_after_process_crash(self):
        coordinator = self.coordinator()
        candidate = coordinator.propose_foreground_feedback(
            "alice", lesson="Recover review after crash.",
            evidence="Candidate was claimed by a process that exited.",
        )
        store = LearningQueueStore(self.db_path)
        self.assertTrue(store.claim("alice", candidate.candidate_id))
        stale = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
        with sqlite3.connect(self.db_path) as db:
            db.execute(
                "UPDATE learning_candidates SET reviewing_at = ? WHERE candidate_id = ?",
                (stale, candidate.candidate_id),
            )
        self.assertTrue(store.claim("alice", candidate.candidate_id))
        self.assertIsNotNone(store.get_reviewing("alice", candidate.candidate_id))
        self.assertTrue(store.resolve("alice", candidate.candidate_id, approved=False))
        self.assertEqual(coordinator.pending("alice"), [])

    def test_durable_dismissal_does_not_write_memory(self):
        coordinator = self.coordinator()
        candidate = coordinator.propose_foreground_feedback(
            "alice", lesson="Discard this lesson.", evidence="User declined.",
        )
        self.assertTrue(coordinator.review("alice", candidate.candidate_id, approved=False))
        self.assertEqual(coordinator.pending("alice"), [])
        self.assertEqual(self.memory.list(), [])



if __name__ == "__main__":
    unittest.main()
