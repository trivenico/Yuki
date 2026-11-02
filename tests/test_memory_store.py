import tempfile
import unittest
from pathlib import Path

from core.memory import MemoryStore


class MemoryStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = MemoryStore(Path(self.temp.name) / "memory.sqlite3")

    def put(self, **kwargs):
        return self.store.put(actor_id="alice", scope_id="project:alpha",
            category="decision", content="Use SQLite for durable local memory",
            **kwargs)

    def test_put_get_and_update(self):
        item = self.put()
        self.assertEqual(self.store.get(actor_id="alice", scope_id="project:alpha",
            memory_id=item.memory_id).content, "Use SQLite for durable local memory")
        updated = self.store.put(actor_id="alice", scope_id="project:alpha",
            category="decision", content="Use encrypted SQLite backups",
            memory_id=item.memory_id)
        self.assertEqual(updated.memory_id, item.memory_id)
        self.assertIn("encrypted", updated.content)

    def test_actor_and_scope_isolation(self):
        item = self.put()
        with self.assertRaises(KeyError):
            self.store.get(actor_id="mallory", scope_id="project:alpha",
                memory_id=item.memory_id)
        with self.assertRaises(KeyError):
            self.store.get(actor_id="alice", scope_id="project:beta",
                memory_id=item.memory_id)
        self.assertEqual(self.store.search(actor_id="mallory",
            scope_id="project:alpha", query="SQLite"), [])

    def test_search_escapes_like_wildcards(self):
        self.put()
        self.store.put(actor_id="alice", scope_id="project:alpha",
            category="note", content="literal 100% reliable")
        self.assertEqual(len(self.store.search(actor_id="alice",
            scope_id="project:alpha", query="100%")), 1)
        self.assertEqual(len(self.store.search(actor_id="alice",
            scope_id="project:alpha", query="SQLite")), 1)

    def test_list_filter_and_delete(self):
        first = self.put()
        self.store.put(actor_id="alice", scope_id="project:alpha",
            category="preference", content="Prefer concise summaries")
        self.assertEqual(len(self.store.list(actor_id="alice",
            scope_id="project:alpha", category="decision")), 1)
        self.assertTrue(self.store.delete(actor_id="alice",
            scope_id="project:alpha", memory_id=first.memory_id))
        self.assertFalse(self.store.delete(actor_id="alice",
            scope_id="project:alpha", memory_id=first.memory_id))

    def test_cannot_overwrite_other_scope_identifier(self):
        item = self.put()
        with self.assertRaises(PermissionError):
            self.store.put(actor_id="alice", scope_id="project:beta",
                category="note", content="attempted overwrite",
                memory_id=item.memory_id)

    def test_validates_category_content_metadata_and_limits(self):
        with self.assertRaises(ValueError):
            self.store.put(actor_id="alice", scope_id="project:alpha",
                category="secret", content="bad category")
        with self.assertRaises(ValueError):
            self.store.put(actor_id="alice", scope_id="project:alpha",
                category="note", content=" ")
        with self.assertRaises(ValueError):
            self.store.put(actor_id="alice", scope_id="project:alpha",
                category="note", content="valid", metadata={"bad": object()})
        with self.assertRaises(ValueError):
            self.store.list(actor_id="alice", scope_id="project:alpha", limit=0)


if __name__ == "__main__":
    unittest.main()
