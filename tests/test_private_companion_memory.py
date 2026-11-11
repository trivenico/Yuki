import os
import stat
import tempfile
import unittest
from pathlib import Path

from companion.memory import MemoryStore
from core.private_file import PrivateFileError, prepare_private_file_path


class PrivateCompanionMemoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    @unittest.skipUnless(os.name == "posix", "POSIX permission semantics only")
    def test_memory_file_is_owner_only_on_creation_and_update(self):
        path = self.root / "memory.json"
        store = MemoryStore(path)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        store.add("private preference", category="preference")
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(store.list()[0]["text"], "private preference")

    @unittest.skipUnless(os.name == "posix", "POSIX permission semantics only")
    def test_existing_permissive_memory_file_is_tightened(self):
        path = self.root / "memory.json"
        path.write_text("[]", encoding="utf-8")
        path.chmod(0o644)
        MemoryStore(path)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    @unittest.skipUnless(hasattr(os, "O_NOFOLLOW"), "O_NOFOLLOW is unavailable")
    def test_memory_symlink_is_rejected_without_touching_target(self):
        target = self.root / "target.json"
        target.write_text("[]", encoding="utf-8")
        link = self.root / "memory.json"
        link.symlink_to(target)
        with self.assertRaises(PrivateFileError):
            MemoryStore(link)
        self.assertEqual(target.read_text(encoding="utf-8"), "[]")

    @unittest.skipUnless(os.name == "posix", "POSIX hard-link semantics only")
    def test_memory_hard_link_is_rejected(self):
        target = self.root / "target.json"
        target.write_text("[]", encoding="utf-8")
        alias = self.root / "memory.json"
        os.link(target, alias)
        with self.assertRaises(PrivateFileError):
            prepare_private_file_path(alias)

    def test_delete_and_clear_remain_explicit_and_durable(self):
        store = MemoryStore(self.root / "memory.json")
        first = store.add("one")
        store.add("two")
        self.assertTrue(store.delete(first["id"]))
        self.assertFalse(store.delete(first["id"]))
        self.assertEqual(store.clear(), 1)
        self.assertEqual(store.list(), [])

    def test_corrupt_memory_is_not_silently_overwritten(self):
        path = self.root / "memory.json"
        path.write_text("{broken", encoding="utf-8")
        store = MemoryStore(path)
        with self.assertRaises(RuntimeError):
            store.add("must not overwrite")
        self.assertEqual(path.read_text(encoding="utf-8"), "{broken")


if __name__ == "__main__":
    unittest.main()
