import unittest
from copy import deepcopy

from core.task_sync_adapter import TaskSyncAdapter
from core.sync_contract import validate_sync_event
from core.sync_client import SyncClientError


def remote_event(revision=1, completed=False, object_id="task_remote0001"):
    return validate_sync_event({
        "event_id": "event_remote0001" if revision == 1 else "event_remote0002",
        "device_id": "device_0000001",
        "collection": "task_records",
        "object_id": object_id,
        "revision": revision,
        "occurred_at": "2026-10-31T10:00:00Z",
        "operation": "upsert",
        "payload": {
            "description": "Remote task",
            "completed": completed,
            "created_at": "2026-10-31T09:00:00+00:00",
        },
    }).to_dict()


class RemoteTaskApplyTests(unittest.TestCase):
    def test_create_assigns_local_id_and_keeps_remote_identity(self):
        tasks = []
        self.assertEqual(TaskSyncAdapter.apply_remote(remote_event(), tasks, lambda _: True), "created")
        self.assertEqual(tasks[0]["id"], 1)
        self.assertEqual(tasks[0]["_yuki_sync"]["object_id"], "task_remote0001")

    def test_duplicate_delivery_is_idempotent(self):
        tasks = []
        event = remote_event()
        TaskSyncAdapter.apply_remote(event, tasks, lambda _: True)
        before = deepcopy(tasks)
        self.assertEqual(TaskSyncAdapter.apply_remote(event, tasks, lambda _: True), "duplicate")
        self.assertEqual(tasks, before)

    def test_next_revision_updates_existing_task(self):
        tasks = []
        TaskSyncAdapter.apply_remote(remote_event(), tasks, lambda _: True)
        self.assertEqual(TaskSyncAdapter.apply_remote(remote_event(2, True), tasks, lambda _: True), "updated")
        self.assertTrue(tasks[0]["completed"])
        self.assertEqual(tasks[0]["_yuki_sync"]["revision"], 2)

    def test_revision_gap_is_rejected(self):
        with self.assertRaises(SyncClientError):
            TaskSyncAdapter.apply_remote(remote_event(2), [], lambda _: True)

    def test_same_revision_with_different_content_is_rejected(self):
        tasks = []
        TaskSyncAdapter.apply_remote(remote_event(), tasks, lambda _: True)
        conflict = remote_event()
        conflict["payload"]["description"] = "different"
        with self.assertRaises(SyncClientError):
            TaskSyncAdapter.apply_remote(conflict, tasks, lambda _: True)

    def test_failed_save_rolls_back_in_memory_mutation(self):
        tasks = []
        event = remote_event()
        with self.assertRaises(SyncClientError):
            TaskSyncAdapter.apply_remote(event, tasks, lambda _: False)
        self.assertEqual(tasks, [])

    def test_delete_is_fail_closed(self):
        event = remote_event()
        event["operation"] = "delete"
        event["payload"] = {}
        with self.assertRaises(SyncClientError):
            TaskSyncAdapter.apply_remote(event, [], lambda _: True)


if __name__ == "__main__":
    unittest.main()
