import tempfile
import unittest
from pathlib import Path

from companion.memory import MemoryStore
from core.learning import LearningCoordinator


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


if __name__ == "__main__":
    unittest.main()
