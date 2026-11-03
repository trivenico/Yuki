import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from core.learning_service import LearningService
from core.user_memory_service import UserMemoryService


class LearningServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.memory = UserMemoryService(root / "memory", root / "consent.sqlite")
        self.learning = LearningService(root / "learning.sqlite", self.memory)
        self.alice = SimpleNamespace(user_id="alice")
        self.bob = SimpleNamespace(user_id="bob")
        self.memory.set_enabled(self.alice, True)
        self.memory.set_enabled(self.bob, True)

    def tearDown(self):
        self.temp.cleanup()

    def test_proposal_does_not_write_memory_until_explicit_approval(self):
        proposal = self.learning.propose(
            self.alice, task_ref="task-1", lesson="Use bounded retries with jitter."
        )
        self.assertEqual(proposal["status"], "pending")
        self.assertEqual(self.memory.list(self.alice), [])
        approved = self.learning.approve(self.alice, proposal["proposal_id"])
        self.assertEqual(approved["status"], "approved")
        self.assertEqual(self.memory.list(self.alice)[0]["category"], "lesson")

    def test_approval_is_idempotent_for_completed_proposal(self):
        proposal = self.learning.propose(
            self.alice, task_ref="task-2", lesson="Validate input before processing."
        )
        first = self.learning.approve(self.alice, proposal["proposal_id"])
        second = self.learning.approve(self.alice, proposal["proposal_id"])
        self.assertEqual(first["memory_id"], second["memory_id"])
        self.assertEqual(len(self.memory.list(self.alice)), 1)

    def test_proposals_are_isolated_by_authenticated_owner(self):
        proposal = self.learning.propose(
            self.alice, task_ref="task-3", lesson="Alice-only lesson."
        )
        self.assertEqual(self.learning.list_pending(self.bob), [])
        with self.assertRaises(KeyError):
            self.learning.approve(self.bob, proposal["proposal_id"])

    def test_rejection_does_not_write_memory(self):
        proposal = self.learning.propose(
            self.alice, task_ref="task-4", lesson="Do not retain this."
        )
        self.assertTrue(self.learning.reject(self.alice, proposal["proposal_id"]))
        self.assertFalse(self.learning.reject(self.alice, proposal["proposal_id"]))
        with self.assertRaises(ValueError):
            self.learning.approve(self.alice, proposal["proposal_id"])
        self.assertEqual(self.memory.list(self.alice), [])

    def test_consent_is_required_to_propose_and_approve(self):
        self.memory.set_enabled(self.alice, False)
        with self.assertRaises(PermissionError):
            self.learning.propose(self.alice, task_ref="task-5", lesson="A lesson")
        self.memory.set_enabled(self.alice, True)
        proposal = self.learning.propose(
            self.alice, task_ref="task-5", lesson="A lesson"
        )
        self.memory.set_enabled(self.alice, False)
        with self.assertRaises(PermissionError):
            self.learning.approve(self.alice, proposal["proposal_id"])

    def test_secret_like_content_is_redacted(self):
        proposal = self.learning.propose(
            self.alice, task_ref="task-6",
            lesson="api_key=supersecretvalue should not be saved"
        )
        self.assertNotIn("supersecretvalue", proposal["lesson"])
        self.assertIn("[REDACTED]", proposal["lesson"])

    def test_task_reference_is_idempotent_but_cannot_be_redefined(self):
        first = self.learning.propose(
            self.alice, task_ref="task-7", lesson="Original lesson."
        )
        again = self.learning.propose(
            self.alice, task_ref="task-7", lesson="Original lesson."
        )
        self.assertEqual(first["proposal_id"], again["proposal_id"])
        with self.assertRaises(ValueError):
            self.learning.propose(
                self.alice, task_ref="task-7", lesson="Different lesson."
            )

    def test_unverified_identity_and_invalid_limits_are_rejected(self):
        with self.assertRaises(PermissionError):
            self.learning.list_pending(SimpleNamespace(user_id=""))
        with self.assertRaises(ValueError):
            self.learning.list_pending(self.alice, limit=True)


if __name__ == "__main__":
    unittest.main()
