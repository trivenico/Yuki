from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from companion.reminder_store import CompanionReminderStore


class CompanionReminderStoreTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.allowed = {"owner-a": {"create", "read", "cancel"}, "owner-b": {"create", "read", "cancel"}}
        self.store = CompanionReminderStore(
            Path(self.tempdir.name) / "reminders.sqlite3",
            authorize=lambda owner, action: action in self.allowed.get(owner, set()),
        )

    def test_reminders_are_persistent_and_owner_isolated(self):
        due = datetime.now(timezone.utc) + timedelta(hours=1)
        item = self.store.create(owner_id="owner-a", text="Review the Yuki roadmap", due_at=due)
        self.assertEqual(len(self.store.list(owner_id="owner-a")), 1)
        self.assertEqual(self.store.list(owner_id="owner-b"), [])
        reopened = CompanionReminderStore(
            Path(self.tempdir.name) / "reminders.sqlite3",
            authorize=lambda owner, action: action in self.allowed.get(owner, set()),
        )
        self.assertEqual(reopened.list(owner_id="owner-a")[0]["reminder_id"], item["reminder_id"])

    def test_cancel_is_owner_scoped_and_idempotently_reports_false(self):
        item = self.store.create(
            owner_id="owner-a", text="Check CI",
            due_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        )
        self.assertFalse(self.store.cancel(owner_id="owner-b", reminder_id=item["reminder_id"]))
        self.assertTrue(self.store.cancel(owner_id="owner-a", reminder_id=item["reminder_id"]))
        self.assertFalse(self.store.cancel(owner_id="owner-a", reminder_id=item["reminder_id"]))
        self.assertEqual(self.store.list(owner_id="owner-a"), [])

    def test_rejects_naive_datetime_and_invalid_text(self):
        with self.assertRaises(ValueError):
            self.store.create(owner_id="owner-a", text="Reminder", due_at=datetime.now())
        with self.assertRaises(ValueError):
            self.store.create(
                owner_id="owner-a", text=" ",
                due_at=datetime.now(timezone.utc) + timedelta(minutes=5),
            )

    def test_authorization_fails_closed(self):
        with self.assertRaises(PermissionError):
            self.store.list(owner_id="owner-a")
        with self.assertRaises(PermissionError):
            self.store.create(
                owner_id="unknown", text="No access",
                due_at=datetime.now(timezone.utc) + timedelta(minutes=5),
            )


if __name__ == "__main__":
    unittest.main()
