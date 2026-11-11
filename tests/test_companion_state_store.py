import json
import os
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
