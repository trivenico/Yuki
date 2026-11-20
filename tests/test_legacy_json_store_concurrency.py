import concurrent.futures
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yuki_learning
import yuki_tasks
import yuki_security


class LegacyJsonStoreConcurrencyTests(unittest.TestCase):
    def test_stale_task_snapshot_cannot_overwrite_a_newer_update(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tasks.json"
            with patch.object(yuki_tasks, "TASKS_FILE", path):
                first_snapshot = yuki_tasks.load_tasks()
                stale_snapshot = yuki_tasks.load_tasks()
                self.assertEqual(yuki_tasks.add_task(first_snapshot, "first"), "Task added.")
                result = yuki_tasks.add_task(stale_snapshot, "stale second")
                self.assertIn("changed", result.lower())
                self.assertEqual(
                    [item["description"] for item in yuki_tasks.load_tasks()],
                    ["first"],
                )

    def test_learning_outcomes_are_not_lost_between_concurrent_writers(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "learning.json"
            with patch.object(yuki_learning, "HISTORY_FILE", path):
                def record(batch):
                    for index in range(10):
                        result = yuki_learning.record_outcome(
                            f"task-{batch}-{index}", "completed"
                        )
                        if result != "Outcome recorded. Lessons are stored locally; the AI model is not trained or modified.":
                            raise AssertionError(result)

                with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
                    list(pool.map(record, range(5)))
                self.assertEqual(len(yuki_learning.load_history()), 50)

    def test_security_audit_events_are_not_lost_between_concurrent_writers(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "audit.json"
            with patch.object(yuki_security, "AUDIT_FILE", path):
                def record(batch):
                    for index in range(10):
                        if not yuki_security.write_audit_event(
                            f"task:{batch}:{index}", "completed"
                        ):
                            raise AssertionError("audit event was not persisted")

                with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
                    list(pool.map(record, range(5)))
                events = yuki_security.load_audit_events()
                self.assertEqual(len(events), 50)


if __name__ == "__main__":
    unittest.main()
