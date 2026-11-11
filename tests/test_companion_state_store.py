import json
import os
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

from companion.state_store import CompanionStateStore


class CompanionStateStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.database = self.root / "state.sqlite3"

    def tearDown(self):
        self.temp.cleanup()

    def test_records_are_scoped_to_owner(self):
        first = CompanionStateStore(self.database, owner_id="owner-a")
        second = CompanionStateStore(self.database, owner_id="owner-b")
        first.add_record("memory", {"id": "same", "text": "private"})
        self.assertEqual(first.list_records("memory")[0]["text"], "private")
        self.assertEqual(second.list_records("memory"), [])

    def test_legacy_json_import_is_once_only_and_non_destructive(self):
        legacy = self.root / "memory.json"
        original = [{"id": "legacy-1", "created_at": "2026-01-01T00:00:00+00:00",
                     "category": "note", "text": "keep me"}]
        legacy.write_text(json.dumps(original), encoding="utf-8")
        store = CompanionStateStore(self.database, owner_id="owner")
        self.assertEqual(store.import_legacy_json("memory", legacy), 1)
        self.assertEqual(store.import_legacy_json("memory", legacy), 0)
        self.assertEqual(store.list_records("memory"), original)
        self.assertEqual(json.loads(legacy.read_text(encoding="utf-8")), original)

    def test_invalid_legacy_file_is_preserved_and_not_marked_migrated(self):
        legacy = self.root / "reminders.json"
        legacy.write_text("{broken", encoding="utf-8")
        store = CompanionStateStore(self.database, owner_id="owner")
        with self.assertRaises(RuntimeError):
            store.import_legacy_json("reminder", legacy)
        self.assertEqual(legacy.read_text(encoding="utf-8"), "{broken")

    def test_concurrent_process_facades_claim_due_reminder_once(self):
        now = datetime.now(timezone.utc)
        first = CompanionStateStore(self.database, owner_id="owner")
        second = CompanionStateStore(self.database, owner_id="owner")
        first.add_record("reminder", {
            "id": "reminder-1", "text": "one time", "due_at": (now - timedelta(seconds=1)).isoformat(),
            "delivered": False,
        })
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda store: store.claim_due_reminders(now), (first, second)))
        self.assertEqual(sum(len(result) for result in results), 1)
        self.assertTrue(first.list_records("reminder")[0]["delivered"])

    def _add_due_reminder(self, store, reminder_id="reminder-outbox"):
        now = datetime.now(timezone.utc)
        store.add_record("reminder", {
            "id": reminder_id, "text": "deliver me",
            "due_at": (now - timedelta(seconds=1)).isoformat(),
            "delivered": False,
        })
        return now

    def test_outbox_enqueue_is_idempotent_and_delivery_marks_reminder(self):
        store = CompanionStateStore(self.database, owner_id="owner")
        now = self._add_due_reminder(store)
        self.assertEqual(store.enqueue_due_reminders(now), 1)
        self.assertEqual(store.enqueue_due_reminders(now), 0)
        self.assertFalse(store.list_records("reminder")[0]["delivered"])
        claimed = store.claim_outbox(now)
        self.assertEqual(len(claimed), 1)
        self.assertEqual(claimed[0]["idempotency_key"], claimed[0]["event_id"])
        self.assertTrue(store.complete_outbox(claimed[0]["event_id"], claimed[0]["lease_token"], now))
        self.assertTrue(store.list_records("reminder")[0]["delivered"])
        self.assertEqual(store.list_outbox()[0]["status"], "delivered")

    def test_deleting_reminder_also_removes_queued_and_leased_payloads(self):
        store = CompanionStateStore(self.database, owner_id="owner")
        now = self._add_due_reminder(store, "delete-me")
        store.enqueue_due_reminders(now)
        claim = store.claim_outbox(now)[0]
        self.assertTrue(store.delete_record("reminder", "delete-me"))
        self.assertEqual(store.list_records("reminder"), [])
        self.assertEqual(store.list_outbox(), [])
        # A worker holding the former lease can no longer complete it.
        self.assertFalse(store.complete_outbox(claim["event_id"], claim["lease_token"], now))

    def test_clearing_reminders_also_clears_outbox_payloads(self):
        store = CompanionStateStore(self.database, owner_id="owner")
        now = self._add_due_reminder(store, "clear-me")
        store.enqueue_due_reminders(now)
        self.assertEqual(store.clear_records("reminder"), 1)
        self.assertEqual(store.list_records("reminder"), [])
        self.assertEqual(store.list_outbox(), [])

    def test_outbox_claim_is_exclusive_across_store_instances(self):
        first = CompanionStateStore(self.database, owner_id="owner")
        second = CompanionStateStore(self.database, owner_id="owner")
        now = self._add_due_reminder(first)
        first.enqueue_due_reminders(now)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda store: store.claim_outbox(now), (first, second)))
        self.assertEqual(sum(len(result) for result in results), 1)

    def test_outbox_expired_lease_is_recovered_and_old_token_is_fenced(self):
        store = CompanionStateStore(self.database, owner_id="owner")
        now = self._add_due_reminder(store)
        store.enqueue_due_reminders(now)
        first = store.claim_outbox(now, lease_seconds=5)[0]
        later = now + timedelta(seconds=6)
        second = store.claim_outbox(later, lease_seconds=5)[0]
        self.assertNotEqual(first["lease_token"], second["lease_token"])
        self.assertFalse(store.complete_outbox(first["event_id"], first["lease_token"], later))
        self.assertTrue(store.complete_outbox(second["event_id"], second["lease_token"], later))

    def test_outbox_failure_retries_then_becomes_terminal(self):
        store = CompanionStateStore(self.database, owner_id="owner")
        now = self._add_due_reminder(store)
        store.enqueue_due_reminders(now)
        first = store.claim_outbox(now)[0]
        self.assertTrue(store.fail_outbox(first["event_id"], first["lease_token"], now,
                                          error="temporary", retry_after_seconds=5, max_attempts=2))
        self.assertEqual(store.claim_outbox(now), [])
        second = store.claim_outbox(now + timedelta(seconds=6))[0]
        self.assertTrue(store.fail_outbox(second["event_id"], second["lease_token"],
                                          now + timedelta(seconds=6), error="permanent",
                                          max_attempts=2))
        self.assertEqual(store.list_outbox()[0]["status"], "failed")
        self.assertEqual(store.claim_outbox(now + timedelta(days=1)), [])

    def test_repeated_worker_crashes_eventually_terminally_fail_outbox(self):
        store = CompanionStateStore(self.database, owner_id="owner")
        now = self._add_due_reminder(store)
        store.enqueue_due_reminders(now)
        first = store.claim_outbox(now, lease_seconds=5, max_attempts=2)[0]
        second_time = now + timedelta(seconds=6)
        second = store.claim_outbox(second_time, lease_seconds=5, max_attempts=2)[0]
        self.assertEqual(second["attempt"], 2)
        third_time = second_time + timedelta(seconds=6)
        self.assertEqual(store.claim_outbox(third_time, lease_seconds=5, max_attempts=2), [])
        self.assertEqual(store.list_outbox()[0]["status"], "failed")
        self.assertFalse(store.complete_outbox(second["event_id"], second["lease_token"], third_time))

    def test_outbox_is_owner_scoped_and_rejects_invalid_bounds(self):
        first = CompanionStateStore(self.database, owner_id="owner-a")
        second = CompanionStateStore(self.database, owner_id="owner-b")
        now = self._add_due_reminder(first)
        first.enqueue_due_reminders(now)
        self.assertEqual(second.list_outbox(), [])
        with self.assertRaises(ValueError):
            first.claim_outbox(now, lease_seconds=1)
        with self.assertRaises(ValueError):
            first.claim_outbox(now, limit=101)

    def test_schema_v1_upgrades_transactionally_to_v2_outbox(self):
        db = sqlite3.connect(self.database)
        db.executescript("""
            CREATE TABLE companion_schema(singleton INTEGER PRIMARY KEY CHECK(singleton=1), version INTEGER NOT NULL);
            INSERT INTO companion_schema(singleton,version) VALUES(1,1);
            CREATE TABLE companion_records(
                owner_id TEXT NOT NULL, kind TEXT NOT NULL, record_id TEXT NOT NULL,
                payload TEXT NOT NULL, created_at TEXT NOT NULL, delivered_at TEXT,
                PRIMARY KEY(owner_id,kind,record_id)
            );
            CREATE TABLE companion_migrations(
                owner_id TEXT NOT NULL, source_name TEXT NOT NULL, migrated_at TEXT NOT NULL,
                imported_count INTEGER NOT NULL, PRIMARY KEY(owner_id,source_name)
            );
        """)
        db.close()
        CompanionStateStore(self.database, owner_id="owner")
        db = sqlite3.connect(self.database)
        self.assertEqual(db.execute("SELECT version FROM companion_schema").fetchone()[0], 2)
        self.assertIsNotNone(db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='companion_outbox'"
        ).fetchone())
        db.close()

    def test_rejects_database_symlink(self):
        target = self.root / "target.sqlite3"
        target.touch()
        link = self.root / "linked.sqlite3"
        try:
            link.symlink_to(target)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks are unavailable on this platform")
        with self.assertRaisesRegex(ValueError, "symlink"):
            CompanionStateStore(link, owner_id="owner")

    def test_posix_database_permissions_are_private(self):
        store = CompanionStateStore(self.database, owner_id="owner")
        if os.name == "posix":
            self.assertEqual(self.database.stat().st_mode & 0o777, 0o600)

    def test_rejects_naive_reminder_claim_time(self):
        store = CompanionStateStore(self.database, owner_id="owner")
        with self.assertRaisesRegex(ValueError, "timezone-aware"):
            store.claim_due_reminders(datetime.now())


if __name__ == "__main__":
    unittest.main()
