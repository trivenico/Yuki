import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from core.user_memory_service import UserMemoryService


class UserMemoryServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.service = UserMemoryService(root / "memory", root / "consent.sqlite")
        self.alice = SimpleNamespace(user_id="verified-alice")
        self.bob = SimpleNamespace(user_id="verified-bob")

    def tearDown(self):
        self.temp.cleanup()

    def test_memory_is_disabled_by_default_and_requires_consent_to_write(self):
        self.assertEqual(self.service.status(self.alice), {"enabled": False})
        with self.assertRaises(PermissionError):
            self.service.remember(self.alice, "private detail")
        self.assertEqual(self.service.list(self.alice), [])

    def test_opt_in_enables_only_that_users_memory(self):
        self.service.set_enabled(self.alice, True)
        item = self.service.remember(self.alice, "Yuki prefers local inference", category="preference")
        self.assertEqual(self.service.search(self.alice, "local inference")[0]["id"], item["id"])
        self.assertEqual(self.service.list(self.bob), [])
        with self.assertRaises(PermissionError):
            self.service.remember(self.bob, "must not be stored")

    def test_edit_export_and_delete_are_user_scoped(self):
        self.service.set_enabled(self.alice, True)
        item = self.service.remember(self.alice, "initial memory")
        self.service.set_enabled(self.bob, True)
        self.service.remember(self.bob, "bob memory")
        with self.assertRaises(KeyError):
            self.service.edit(self.bob, item["id"], "attempted overwrite")
        updated = self.service.edit(self.alice, item["id"], "edited memory", category="lesson")
        self.assertEqual(updated["text"], "edited memory")
        exported = self.service.export(self.alice)
        self.assertEqual(len(exported["memories"]), 1)
        self.assertTrue(self.service.forget(self.alice, item["id"]))
        self.assertEqual(self.service.list(self.alice), [])
        self.assertEqual(self.service.list(self.bob)[0]["text"], "bob memory")

    def test_disabling_consent_stops_reads_and_writes_without_silent_deletion(self):
        self.service.set_enabled(self.alice, True)
        item = self.service.remember(self.alice, "retain until explicit deletion")
        self.service.set_enabled(self.alice, False)
        self.assertEqual(self.service.list(self.alice), [])
        with self.assertRaises(PermissionError):
            self.service.remember(self.alice, "blocked")
        # Export clearly reports disabled state and does not expose memories.
        exported = self.service.export(self.alice)
        self.assertFalse(exported["memory_enabled"])
        self.assertEqual(exported["memories"], [])
        # Explicit deletion remains available while disabled.
        self.assertTrue(self.service.forget(self.alice, item["id"]))

    def test_edit_rejects_invalid_category_types_without_corrupting_memory(self):
        self.service.set_enabled(self.alice, True)
        item = self.service.remember(self.alice, "stable memory")
        with self.assertRaises(ValueError):
            self.service.edit(self.alice, item["id"], "changed", category=["invalid"])
        current = self.service.list(self.alice)[0]
        self.assertEqual(current["text"], "stable memory")
        self.assertEqual(current["category"], "note")

    def test_rejects_unverified_actor_shapes(self):
        with self.assertRaises(PermissionError):
            self.service.status(SimpleNamespace(user_id=""))


if __name__ == "__main__":
    unittest.main()
