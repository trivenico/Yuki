import json
import tempfile
import unittest
from pathlib import Path

import yuki_tasks


class YukiTasksTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_path = yuki_tasks.TASKS_FILE
        yuki_tasks.TASKS_FILE = Path(self.temp_dir.name) / "tasks.json"

    def tearDown(self):
        yuki_tasks.TASKS_FILE = self.original_path
        self.temp_dir.cleanup()

    def test_load_missing_file_returns_empty_list(self):
        self.assertEqual(yuki_tasks.load_tasks(), [])

    def test_add_complete_and_delete_use_snapshot_updates(self):
        tasks = yuki_tasks.load_tasks()
        self.assertEqual(yuki_tasks.add_task(tasks, "Review deployment"), "Task added.")
        self.assertEqual(tasks[0]["description"], "Review deployment")
        self.assertEqual(yuki_tasks.complete_task(tasks, tasks[0]["id"]), "Task completed.")
        self.assertTrue(yuki_tasks.load_tasks()[0]["completed"])
        self.assertEqual(yuki_tasks.delete_task(tasks, tasks[0]["id"]), "Task deleted.")
        self.assertEqual(yuki_tasks.load_tasks(), [])

    def test_stale_snapshot_cannot_overwrite_newer_task_list(self):
        first = yuki_tasks.load_tasks()
        second = yuki_tasks.load_tasks()
        self.assertEqual(yuki_tasks.add_task(first, "Created by first writer"), "Task added.")
        self.assertEqual(
            yuki_tasks.add_task(second, "Created by stale writer"),
            "Task list changed; reload it before applying this update.",
        )
        self.assertEqual([item["description"] for item in yuki_tasks.load_tasks()],
                         ["Created by first writer"])

    def test_save_requires_current_snapshot(self):
        original = yuki_tasks.load_tasks()
        self.assertEqual(yuki_tasks.add_task(original, "Concurrent update"), "Task added.")
        stale_candidate = [{"id": 1, "description": "stale", "completed": False}]
        self.assertFalse(yuki_tasks.save_tasks(stale_candidate, expected_snapshot=[]))
        current = yuki_tasks.load_tasks()
        replacement = [{"id": 1, "description": "reviewed", "completed": False}]
        self.assertTrue(yuki_tasks.save_tasks(replacement, expected_snapshot=current))
        self.assertEqual(yuki_tasks.load_tasks(), replacement)

    def test_malformed_entry_fails_closed_without_discarding_data(self):
        malformed = [
            {"id": 1, "description": "valid", "completed": False},
            {"id": "bad", "description": "must not disappear", "completed": False},
        ]
        yuki_tasks.TASKS_FILE.write_text(json.dumps(malformed), encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "invalid task"):
            yuki_tasks.load_tasks()
        tasks = []
        self.assertEqual(yuki_tasks.add_task(tasks, "new task"), "Could not save the task.")
        self.assertEqual(json.loads(yuki_tasks.TASKS_FILE.read_text(encoding="utf-8")), malformed)

    def test_duplicate_ids_fail_closed(self):
        duplicate = [
            {"id": 1, "description": "one", "completed": False},
            {"id": 1, "description": "two", "completed": False},
        ]
        yuki_tasks.TASKS_FILE.write_text(json.dumps(duplicate), encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "duplicate task IDs"):
            yuki_tasks.load_tasks()

    def test_save_rejects_invalid_and_duplicate_task_ids(self):
        current = yuki_tasks.load_tasks()
        invalid = [{"id": 0, "description": "bad", "completed": False}]
        duplicate = [
            {"id": 1, "description": "one", "completed": False},
            {"id": 1, "description": "two", "completed": False},
        ]
        self.assertFalse(yuki_tasks.save_tasks(invalid, expected_snapshot=current))
        self.assertFalse(yuki_tasks.save_tasks(duplicate, expected_snapshot=current))
        self.assertEqual(yuki_tasks.load_tasks(), [])


if __name__ == "__main__":
    unittest.main()
