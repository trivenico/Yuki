from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from companion.reminder_store import CompanionReminderStore


class CompanionReminderStoreTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.database = Path(self.tempdir.name) / "reminders.sqlite3"
        self.allowed = {"owner-a": {"create", "read", "cancel"}, "owner-b": {"create", "read", "cancel"}}
        self.store = CompanionReminderStore(
            self.database,
            authorize=lambda owner, action: action in self.allowed.get(owner, set()),
        )

    def reopen(self):
        return CompanionReminderStore(
            self.database,
            authorize=lambda owner, action: action in self.allowed.get(owner, set()),
        )

    def test_reminders_are_persistent_and_owner_isolated(self):
        due = datetime.now(timezone.utc) + timedelta(hours=1)
        item = self.store.create(owner_id="owner-a", text="Review the Yuki roadmap", due_at=due)
        self.assertEqual(len(self.store.list(owner_id="owner-a")), 1)
        self.assertEqual(self.store.list(owner_id="owner-b"), [])
        self.assertEqual(self.reopen().list(owner_id="owner-a")[0]["reminder_id"], item["reminder_id"])

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
        self.allowed["owner-a"] = set()
        with self.assertRaises(PermissionError):
            self.store.list(owner_id="owner-a")
        with self.assertRaises(PermissionError):
            self.store.create(
                owner_id="unknown", text="No access",
                due_at=datetime.now(timezone.utc) + timedelta(minutes=5),
            )

    def test_due_claim_is_leased_and_acknowledgement_is_idempotent(self):
        now = datetime(2030, 1, 1, tzinfo=timezone.utc)
        item = self.store.create(owner_id="owner-a", text="Send a notification", due_at=now)
        first = self.store.claim_due(now=now)
        self.assertEqual(len(first), 1)
        self.assertEqual(first[0]["reminder_id"], item["reminder_id"])
        self.assertEqual(first[0]["owner_id"], "owner-a")
        self.assertEqual(self.store.claim_due(now=now), [])
        self.assertTrue(self.store.acknowledge_delivery(
            reminder_id=item["reminder_id"], lease_token=first[0]["lease_token"], delivered_at=now,
        ))
        self.assertFalse(self.store.acknowledge_delivery(
            reminder_id=item["reminder_id"], lease_token=first[0]["lease_token"], delivered_at=now,
        ))
        self.assertEqual(self.store.list(owner_id="owner-a"), [])

    def test_expired_lease_can_be_reclaimed_after_restart_and_stale_token_fails(self):
        now = datetime(2030, 1, 1, tzinfo=timezone.utc)
        item = self.store.create(owner_id="owner-a", text="Recover after crash", due_at=now)
        first = self.store.claim_due(now=now, lease_seconds=5)[0]
        restarted = self.reopen()
        second = restarted.claim_due(now=now + timedelta(seconds=6), lease_seconds=5)[0]
        self.assertNotEqual(first["lease_token"], second["lease_token"])
        self.assertFalse(restarted.acknowledge_delivery(
            reminder_id=item["reminder_id"], lease_token=first["lease_token"],
        ))
        self.assertTrue(restarted.acknowledge_delivery(
            reminder_id=item["reminder_id"], lease_token=second["lease_token"],
        ))

    def test_retry_backoff_and_attempt_limit(self):
        now = datetime(2030, 1, 1, tzinfo=timezone.utc)
        item = self.store.create(owner_id="owner-a", text="Retry delivery", due_at=now)
        for attempt in range(1, self.store.MAX_DELIVERY_ATTEMPTS + 1):
            claim = self.store.claim_due(now=now)[0]
            result = self.store.record_delivery_failure(
                reminder_id=item["reminder_id"], lease_token=claim["lease_token"], failed_at=now,
            )
            self.assertEqual(result["attempts"], attempt)
            self.assertEqual(result["exhausted"], attempt == self.store.MAX_DELIVERY_ATTEMPTS)
            if attempt < self.store.MAX_DELIVERY_ATTEMPTS:
                self.assertEqual(self.store.claim_due(now=now), [])
                now += timedelta(seconds=2 ** attempt * 5)
        self.assertEqual(self.store.claim_due(now=now + timedelta(days=1)), [])

    def test_concurrent_dispatchers_cannot_claim_same_reminder(self):
        now = datetime(2030, 1, 1, tzinfo=timezone.utc)
        self.store.create(owner_id="owner-a", text="Only one active lease", due_at=now)
        stores = [self.reopen() for _ in range(8)]
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda store: store.claim_due(now=now), stores))
        claims = [claim for batch in results for claim in batch]
        self.assertEqual(len(claims), 1)


if __name__ == "__main__":
    unittest.main()
