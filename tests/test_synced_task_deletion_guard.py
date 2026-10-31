import unittest

from yuki_tasks import delete_task


class SyncedTaskDeletionGuardTests(unittest.TestCase):
    def test_synced_task_cannot_be_deleted_before_tombstones_exist(self):
        tasks = [{
            "id": 1,
            "description": "Shared task",
            "completed": False,
            "_yuki_sync": {
                "object_id": "task_remote0001",
                "revision": 1,
                "fingerprint": "abc",
            },
        }]
        before = [dict(tasks[0])]
        result = delete_task(tasks, 1)
        self.assertIn("Deletion is disabled", result)
        self.assertEqual(tasks, before)

    def test_unsynced_task_keeps_existing_delete_behavior(self):
        tasks = [{"id": 1, "description": "Local task", "completed": False}]
        result = delete_task(tasks, 1)
        self.assertIn("deleted", result.lower())
        self.assertEqual(tasks, [])


if __name__ == "__main__":
    unittest.main()
