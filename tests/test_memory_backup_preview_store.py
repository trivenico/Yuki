"""Read-only owner-scoped conflict inspection tests."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from core.conversation_memory_store import ConversationMemoryStore


class MemoryBackupPreviewStoreTests(unittest.TestCase):
    def test_conflict_inspection_is_owner_scoped_and_does_not_mutate_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ConversationMemoryStore(Path(directory) / "memory.sqlite3")
            alice = store.remember(owner_id="alice", content="Alice memory")
            store.remember(owner_id="bob", content="Bob memory")
            before = store.list(owner_id="alice")
            conflicts = store.inspect_import_conflicts(
                owner_id="alice",
                memory_ids=(alice["memory_id"], "not-present"),
            )
            self.assertEqual(conflicts, 1)
            self.assertEqual(store.list(owner_id="alice"), before)
            self.assertEqual(store.inspect_import_conflicts(
                owner_id="bob", memory_ids=(alice["memory_id"],),
            ), 0)

    def test_conflict_inspection_rejects_unbounded_or_invalid_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ConversationMemoryStore(Path(directory) / "memory.sqlite3")
            with self.assertRaises(ValueError):
                store.inspect_import_conflicts(owner_id="alice", memory_ids=("x",) * 2001)
            with self.assertRaises(ValueError):
                store.inspect_import_conflicts(owner_id="alice", memory_ids=("x", None))


if __name__ == "__main__":
    unittest.main()
