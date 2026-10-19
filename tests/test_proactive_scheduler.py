import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from companion.proactive import ReminderQueue, ReminderSchedulerThread


class SchedulerThreadTests(unittest.TestCase):
    def test_background_scheduler_fires_due_reminder(self):
        with tempfile.TemporaryDirectory() as tmp:
            queue = ReminderQueue(Path(tmp) / "reminders.json")
            due_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            queue.add("Test background reminder", due_at)

            fired = []
            scheduler = ReminderSchedulerThread(queue, on_due=lambda item: fired.append(item), interval_seconds=0.1)
            scheduler.start()
            time.sleep(0.3)
            scheduler.stop()

            self.assertGreaterEqual(len(fired), 1)
            self.assertEqual(fired[0]["text"], "Test background reminder")


if __name__ == "__main__":
    unittest.main()
