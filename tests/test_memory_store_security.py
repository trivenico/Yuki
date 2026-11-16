import os
import stat
import tempfile
import unittest
from pathlib import Path

from companion.memory import MemoryStore


class MemoryStoreSecurityTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.path = Path(self.tempdir.name) / "private" / "memory.json"

    def test_new_memory_file_is_initialized_and_private(self):
        store = MemoryStore(self.path)
        self.assertEqual(store.list(), [])
        self.assertEqual(self.path.read_text(encoding="utf-8"), "[]")
        if os.name == "posix":
            self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)
            self.assertEqual(stat.S_IMODE(self.path.parent.stat().st_mode), 0o700)

    def test_existing_permissive_file_is_restricted(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text("[]", encoding="utf-8")
        if os.name == "posix":
            self.path.chmod(0o644)
        MemoryStore(self.path)
        if os.name == "posix":
            self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)

    def test_add_list_delete_and_clear(self):
        store = MemoryStore(self.path)
        item = store.add("Remember this", category="preference")
        self.assertEqual(store.list()[0]["text"], "Remember this")
        self.assertTrue(store.delete(item["id"]))
        self.assertFalse(store.delete(item["id"]))
        store.add("One")
        store.add("Two")
        self.assertEqual(store.clear(), 2)
        self.assertEqual(store.list(), [])

    def test_rejects_symbolic_link(self):
        if not hasattr(os, "O_NOFOLLOW"):
            self.skipTest("O_NOFOLLOW unavailable")
        target = Path(self.tempdir.name) / "target.json"
        target.write_text("[]", encoding="utf-8")
        self.path.parent.mkdir(parents=True)
        self.path.symlink_to(target)
        with self.assertRaises(RuntimeError):
            MemoryStore(self.path)
        self.assertEqual(target.read_text(encoding="utf-8"), "[]")

    def test_rejects_hard_link(self):
        if os.name != "posix":
            self.skipTest("POSIX hard-link semantics")
        self.path.parent.mkdir(parents=True)
        target = Path(self.tempdir.name) / "target.json"
        target.write_text("[]", encoding="utf-8")
        os.link(target, self.path)
        with self.assertRaises(RuntimeError):
            MemoryStore(self.path)

    def test_corrupt_existing_file_is_not_overwritten(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text("{broken", encoding="utf-8")
        with self.assertRaises(RuntimeError):
            MemoryStore(self.path).list()
        self.assertEqual(self.path.read_text(encoding="utf-8"), "{broken")

    def test_invalid_category_and_text_are_rejected(self):
        store = MemoryStore(self.path)
        with self.assertRaises(ValueError):
            store.add("text", category="secret")
        with self.assertRaises(ValueError):
            store.add("x" * (MemoryStore.MAX_TEXT + 1))


if __name__ == "__main__":
    unittest.main()
