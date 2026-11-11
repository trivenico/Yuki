import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from companion.relationship_mode import (
    AffectionLevel,
    RelationshipMode,
    RelationshipModeSettings,
)
from companion.relationship_settings_store import RelationshipSettingsStore


class RelationshipSettingsStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.database = self.root / "relationship.sqlite3"

    def tearDown(self):
        self.temp.cleanup()

    def test_missing_settings_load_safe_defaults(self):
        store = RelationshipSettingsStore(self.database, owner_id="owner")
        self.assertEqual(store.load(), RelationshipModeSettings())

    def test_save_and_reload_romantic_settings(self):
        store = RelationshipSettingsStore(self.database, owner_id="owner")
        settings = RelationshipModeSettings(
            mode=RelationshipMode.ROMANTIC,
            affection=AffectionLevel.HIGH,
            adult_confirmed=True,
            memory_enabled=True,
            proactive_checkins_enabled=True,
            quiet_hours_start="22:00",
            quiet_hours_end="08:00",
            topics_to_avoid=("work stress",),
        )
        store.save(settings)
        self.assertEqual(store.load(), settings)

    def test_settings_are_isolated_by_owner(self):
        first = RelationshipSettingsStore(self.database, owner_id="owner-a")
        second = RelationshipSettingsStore(self.database, owner_id="owner-b")
        first.save(RelationshipModeSettings(mode=RelationshipMode.FRIENDLY))
        self.assertEqual(second.load(), RelationshipModeSettings())
        self.assertEqual(first.load().mode, RelationshipMode.FRIENDLY)

    def test_save_replaces_preferences_atomically_and_reset_restores_defaults(self):
        store = RelationshipSettingsStore(self.database, owner_id="owner")
        store.save(RelationshipModeSettings(mode=RelationshipMode.FRIENDLY))
        store.save(RelationshipModeSettings())
        self.assertEqual(store.load(), RelationshipModeSettings())
        store.save(RelationshipModeSettings(mode=RelationshipMode.FRIENDLY))
        store.reset()
        self.assertEqual(store.load(), RelationshipModeSettings())

    def test_invalid_romantic_settings_are_rejected_before_write(self):
        store = RelationshipSettingsStore(self.database, owner_id="owner")
        with self.assertRaises(ValueError):
            RelationshipModeSettings(mode=RelationshipMode.ROMANTIC)
        self.assertEqual(store.load(), RelationshipModeSettings())

    def test_corrupt_or_unknown_payload_fails_closed(self):
        store = RelationshipSettingsStore(self.database, owner_id="owner")
        db = sqlite3.connect(self.database)
        db.execute(
            "INSERT INTO relationship_settings(owner_id,payload) VALUES(?,?)",
            ("owner", '{"mode":"romantic","unexpected":true}'),
        )
        db.commit()
        db.close()
        with self.assertRaisesRegex(RuntimeError, "invalid"):
            store.load()

    def test_rejects_database_symlink(self):
        target = self.root / "target.sqlite3"
        target.touch()
        link = self.root / "linked.sqlite3"
        try:
            link.symlink_to(target)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks are unavailable on this platform")
        with self.assertRaisesRegex(ValueError, "symlink"):
            RelationshipSettingsStore(link, owner_id="owner")

    def test_posix_database_permissions_are_private(self):
        RelationshipSettingsStore(self.database, owner_id="owner")
        if os.name == "posix":
            self.assertEqual(self.database.stat().st_mode & 0o777, 0o600)


if __name__ == "__main__":
    unittest.main()
