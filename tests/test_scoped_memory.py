import tempfile
import unittest
from pathlib import Path

from core.scoped_memory import ScopedMemory, actor_storage_key


class ScopedMemoryTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name) / "users"

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_actor_namespaces_are_stable_and_distinct(self):
        self.assertEqual(actor_storage_key("account-a"), actor_storage_key("account-a"))
        self.assertNotEqual(actor_storage_key("account-a"), actor_storage_key("account-b"))
        first = ScopedMemory("account-a", root=self.root)
        second = ScopedMemory("account-b", root=self.root)
        first.remember("private project alpha", category="note")
        self.assertEqual(first.search("private project"), first.search("private project"))
        self.assertEqual(second.list(), [])
        self.assertEqual(first.list()[0]["text"], "private project alpha")

    def test_same_actor_reopens_existing_memories(self):
        first = ScopedMemory("stable-user", root=self.root)
        item = first.remember("remembered locally", category="lesson")
        second = ScopedMemory("stable-user", root=self.root)
        self.assertEqual(second.list()[0]["id"], item["id"])
        self.assertEqual(second.search("remembered locally")[0]["id"], item["id"])

    def test_delete_is_scoped_to_actor(self):
        first = ScopedMemory("user-one", root=self.root)
        second = ScopedMemory("user-two", root=self.root)
        item = first.remember("one user's secret")
        second.remember("another user's secret")
        self.assertFalse(second.forget(item["id"]))
        self.assertTrue(first.forget(item["id"]))
        self.assertEqual(first.list(), [])

    def test_rejects_empty_or_control_character_actor_ids(self):
        for actor in ("", "  ", "user\nforged", "user\x00id"):
            with self.subTest(actor=repr(actor)), self.assertRaises(ValueError):
                ScopedMemory(actor, root=self.root)

    def test_filesystem_namespace_does_not_expose_raw_actor_id(self):
        ScopedMemory("email@example.invalid", root=self.root)
        paths = list(self.root.iterdir())
        self.assertEqual(len(paths), 1)
        self.assertNotIn("email", paths[0].name)
        self.assertEqual(len(paths[0].name), 64)


if __name__ == "__main__":
    unittest.main()
