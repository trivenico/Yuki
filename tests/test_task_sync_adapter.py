import unittest
from copy import deepcopy

from core.task_sync_adapter import TaskSyncAdapter


def sample_task():
    return {"id": 12, "description": "Prepare release", "completed": False,
            "created_at": "2026-10-31T10:00:00+00:00", "api_key": "never-export"}


class TaskSyncAdapterTests(unittest.TestCase):
    def test_preview_does_not_mutate_tasks(self):
        tasks = [sample_task()]
        before = deepcopy(tasks)
        self.assertEqual(TaskSyncAdapter.preview(tasks), {"total": 1, "tracked": 0})
        self.assertEqual(tasks, before)

    def test_queue_exports_only_allowlisted_fields(self):
        tasks = [sample_task()]
        events = []
        count = TaskSyncAdapter.queue(tasks, "device_0000001", lambda event: events.append(event) or True)
        self.assertEqual(count, 1)
        self.assertEqual(events[0]["collection"], "task_records")
        self.assertEqual(set(events[0]["payload"]), {"description", "completed", "created_at"})
        self.assertNotIn("api_key", str(events[0]))
        self.assertEqual(tasks[0]["_yuki_sync"]["revision"], 1)

    def test_repeated_queue_is_idempotent_after_metadata_persisted(self):
        tasks = [sample_task()]
        events = []
        TaskSyncAdapter.queue(tasks, "device_0000001", lambda event: events.append(event) or True)
        self.assertEqual(TaskSyncAdapter.queue(tasks, "device_0000001", lambda event: events.append(event) or True), 0)
        self.assertEqual(len(events), 1)

    def test_retry_after_metadata_loss_recreates_identical_event(self):
        task = sample_task()
        first = []
        TaskSyncAdapter.queue([task], "device_0000001", lambda event: first.append(event) or True)
        retry = []
        TaskSyncAdapter.queue([sample_task()], "device_0000001", lambda event: retry.append(event) or False)
        self.assertEqual(first, retry)

    def test_changed_task_creates_next_revision(self):
        tasks = [sample_task()]
        events = []
        TaskSyncAdapter.queue(tasks, "device_0000001", lambda event: events.append(event) or True)
        tasks[0]["completed"] = True
        TaskSyncAdapter.queue(tasks, "device_0000001", lambda event: events.append(event) or True)
        self.assertEqual([event["revision"] for event in events], [1, 2])
        self.assertNotEqual(events[0]["event_id"], events[1]["event_id"])

    def test_forwarded_remote_task_preserves_shared_object_identity(self):
        task = sample_task()
        task["_yuki_sync"] = {
            "object_id": "task_remote0001", "revision": 4, "fingerprint": "old"
        }
        events = []
        TaskSyncAdapter.queue([task], "device_0000002", lambda event: events.append(event) or True)
        self.assertEqual(events[0]["object_id"], "task_remote0001")
        self.assertEqual(events[0]["revision"], 5)

    def test_invalid_local_sync_metadata_does_not_crash(self):
        task = sample_task()
        task["_yuki_sync"] = {"revision": "not-an-integer", "fingerprint": "old"}
        events = []
        TaskSyncAdapter.queue([task], "device_0000001", lambda event: events.append(event) or True)
        self.assertEqual(events[0]["revision"], 1)


if __name__ == "__main__":
    unittest.main()
