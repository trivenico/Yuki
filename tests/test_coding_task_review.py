import tempfile
import unittest
import hashlib
from pathlib import Path

from core.coding_task_review import CodingTaskReviewError, review_coding_task_changes
from core.coding_tasks import CodingTaskConflict, CodingTaskStore


PATCH = (
    "diff --git a/src/app.py b/src/app.py\n"
    "--- a/src/app.py\n"
    "+++ b/src/app.py\n"
    "@@ -1 +1 @@\n"
    "-old\n"
    "+new\n"
)


class CodingTaskReviewIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = CodingTaskStore(str(Path(self.tmp.name) / "tasks.sqlite3"))
        self.task = self.store.create(
            idempotency_key="review-task-1",
            goal="Review a proposed Python change",
            workspace=self.tmp.name,
        )
        self.task = self.store.attach_patch(
            self.task.task_id, expected_version=self.task.version, patch=PATCH
        )

    def test_clean_review_attaches_digest_bound_evidence_and_requires_human_approval(self):
        files = {"src/app.py": "def answer():\n    return 42\n"}
        report, updated = review_coding_task_changes(
            self.store,
            task_id=self.task.task_id,
            expected_version=self.task.version,
            expected_patch_sha256=self.task.patch_sha256,
            proposed_files=files,
        )
        self.assertTrue(report.eligible_for_human_review)
        self.assertEqual(updated.state, "ready_for_review")
        self.assertIn(self.task.patch_sha256, updated.review)
        self.assertIn('"merge_authorized": false', updated.review)
        self.assertNotEqual(updated.state, "approved")

    def test_blocking_finding_keeps_task_in_draft(self):
        files = {"src/app.py": "eval(user_input)\n"}
        report, unchanged = review_coding_task_changes(
            self.store, task_id=self.task.task_id,
            expected_version=self.task.version,
            expected_patch_sha256=self.task.patch_sha256,
            proposed_files=files,
        )
        self.assertFalse(report.eligible_for_human_review)
        self.assertEqual(unchanged.state, "draft")
        self.assertEqual(unchanged.version, self.task.version)

    def test_stale_task_version_is_rejected(self):
        with self.assertRaises(CodingTaskConflict):
            review_coding_task_changes(
                self.store, task_id=self.task.task_id,
                expected_version=self.task.version + 1,
                expected_patch_sha256=self.task.patch_sha256,
                proposed_files={"src/app.py": "pass\n"},
            )

    def test_wrong_patch_digest_is_rejected(self):
        wrong = hashlib.sha256(b"not the patch").hexdigest()
        with self.assertRaises(CodingTaskConflict):
            review_coding_task_changes(
                self.store, task_id=self.task.task_id,
                expected_version=self.task.version,
                expected_patch_sha256=wrong,
                proposed_files={"src/app.py": "pass\n"},
            )

    def test_missing_patch_is_rejected(self):
        task = self.store.create(
            idempotency_key="review-task-no-patch",
            goal="No patch",
            workspace=self.tmp.name,
        )
        with self.assertRaises(CodingTaskReviewError):
            review_coding_task_changes(
                self.store, task_id=task.task_id,
                expected_version=task.version,
                expected_patch_sha256="a" * 64,
                proposed_files={"src/app.py": "pass\n"},
            )

    def test_human_approval_remains_a_separate_transition(self):
        _, ready = review_coding_task_changes(
            self.store, task_id=self.task.task_id,
            expected_version=self.task.version,
            expected_patch_sha256=self.task.patch_sha256,
            proposed_files={"src/app.py": "pass\n"},
        )
        approved = self.store.transition(
            ready.task_id, expected_version=ready.version,
            new_state="approved", review="Human reviewed patch and CI evidence.",
        )
        self.assertEqual(approved.state, "approved")


if __name__ == "__main__":
    unittest.main()
