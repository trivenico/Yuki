import tempfile
import unittest
from pathlib import Path

from core.sync_client import SyncOutbox
from core.task_sync_adapter import TaskSyncAdapter


class TaskSyncOutboxIntegrationTests(unittest.TestCase):
    def test_task_events_are_accepted_by_real_outbox_and_deduplicated(self):
        with tempfile.TemporaryDirectory() as tmp:
            outbox = SyncOutbox(Path(tmp) / "outbox.sqlite3")
            tasks = [{"id": 7, "description": "Review PR", "completed": False,
                      "created_at": "2026-10-31T10:00:00+00:00"}]
            try:
                first = TaskSyncAdapter.queue(tasks, "device_0000001", outbox.enqueue)
                second = TaskSyncAdapter.queue(tasks, "device_0000001", outbox.enqueue)
                self.assertEqual(first, 1)
                self.assertEqual(second, 0)
                self.assertEqual(outbox.counts()["pending"], 1)
                event = outbox.ready()[0]
                self.assertEqual(event["collection"], "task_records")
            finally:
                outbox.close()


if __name__ == "__main__":
    unittest.main()
