import tempfile
import unittest
from pathlib import Path

from core.coding_tasks import (
    CodingTaskConflict, CodingTaskStore, CodingTaskTransitionError,
)


class CodingTaskStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = CodingTaskStore(str(Path(self.tmp.name) / "coding.sqlite3"))

    def create(self, key="task-1"):
        return self.store.create(idempotency_key=key, goal="Fix the bug", workspace=str(Path(self.tmp.name)))

    def test_create_and_idempotent_retry(self):
        first = self.create()
        second = self.create()
        self.assertEqual(first.task_id, second.task_id)
        self.assertEqual(first.state, "draft")
        self.assertEqual(first.version, 0)

    def test_idempotency_key_payload_mismatch_fails(self):
        self.create()
        with self.assertRaises(CodingTaskConflict):
            self.store.create(idempotency_key="task-1", goal="Different goal", workspace=str(Path(self.tmp.name)))

    def test_plan_is_bounded_and_versioned(self):
        task = self.create()
        task = self.store.update_plan(task.task_id, expected_version=task.version,
                                     plan=[{"title": "Inspect", "description": "Read code and tests"}])
        self.assertEqual(task.version, 1)
        self.assertEqual(task.plan[0]["title"], "Inspect")
        with self.assertRaises(CodingTaskConflict):
            self.store.update_plan(task.task_id, expected_version=0, plan=[])

    def test_patch_must_be_diff_and_integrity_is_checked(self):
        task = self.create()
        patch = "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-a\n+b\n"
        task = self.store.attach_patch(task.task_id, expected_version=task.version, patch=patch)
        self.assertTrue(self.store.patch_integrity_ok(task.task_id))
        with self.assertRaises(ValueError):
            self.store.attach_patch(task.task_id, expected_version=task.version, patch="not a diff")

    def test_review_lifecycle_requires_review_and_no_execution(self):
        task = self.create()
        task = self.store.update_plan(task.task_id, expected_version=task.version,
                                      plan=[{"title": "Test", "description": "Add regression test"}])
        task = self.store.attach_patch(task.task_id, expected_version=task.version,
                                       patch="diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-a\n+b\n")
        task = self.store.transition(task.task_id, expected_version=task.version, new_state="ready_for_review")
        with self.assertRaises(CodingTaskTransitionError):
            self.store.transition(task.task_id, expected_version=task.version, new_state="completed")
        task = self.store.transition(task.task_id, expected_version=task.version, new_state="approved",
                                     review="Human inspected patch and test evidence.")
        self.assertEqual(task.state, "approved")
        task = self.store.transition(task.task_id, expected_version=task.version, new_state="completed",
                                     review="Merged separately after CI and human approval.")
        self.assertEqual(task.state, "completed")

    def test_invalid_plan_and_patch_bounds(self):
        task = self.create()
        with self.assertRaises(ValueError):
            self.store.update_plan(task.task_id, expected_version=task.version,
                                   plan=[{"title": "x", "description": "y", "run_shell": "rm -rf"}])
        with self.assertRaises(ValueError):
            self.store.attach_patch(task.task_id, expected_version=task.version, patch="x" * (256 * 1024 + 1))

    def test_invalid_limits_and_states(self):
        for value in (True, 0, 201, 1.5):
            with self.assertRaises(ValueError):
                self.store.list_tasks(limit=value)
        with self.assertRaises(ValueError):
            self.store.list_tasks(states=("unknown",))


if __name__ == "__main__":
    unittest.main()
