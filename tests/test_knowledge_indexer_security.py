import os
import tempfile
import unittest
from pathlib import Path

from knowledge.indexer import KnowledgeIndexer


class _Memory:
    def __init__(self):
        self.items = []

    def remember(self, text, *, category):
        item = {"id": str(len(self.items) + 1)}
        self.items.append((text, category))
        return item


@unittest.skipUnless(
    os.name == "posix" and hasattr(os, "O_NOFOLLOW") and hasattr(os, "O_DIRECTORY"),
    "descriptor-safe traversal requires POSIX no-follow directory opens",
)
class KnowledgeIndexerSecurityTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.root = Path(self.tempdir.name) / "workspace"
        self.root.mkdir()
        self.memory = _Memory()
        self.indexer = KnowledgeIndexer(self.memory)

    def test_indexes_regular_file_inside_workspace(self):
        (self.root / "notes.md").write_text("private project notes", encoding="utf-8")
        result = self.indexer.index_file("notes.md", workspace=self.root)
        self.assertEqual(result["chunks_indexed"], 1)
        self.assertIn("private project notes", self.memory.items[0][0])

    def test_rejects_symlink_to_file_outside_workspace(self):
        outside = Path(self.tempdir.name) / "outside.md"
        outside.write_text("must not be indexed", encoding="utf-8")
        (self.root / "linked.md").symlink_to(outside)
        with self.assertRaises(ValueError):
            self.indexer.index_file("linked.md", workspace=self.root)
        self.assertEqual(self.memory.items, [])

    def test_rejects_symlinked_parent_directory(self):
        outside = Path(self.tempdir.name) / "outside"
        outside.mkdir()
        (outside / "secret.md").write_text("must not be indexed", encoding="utf-8")
        (self.root / "linked").symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.indexer.index_file("linked/secret.md", workspace=self.root)
        self.assertEqual(self.memory.items, [])

    def test_rejects_path_traversal(self):
        outside = Path(self.tempdir.name) / "outside.md"
        outside.write_text("must not be indexed", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.indexer.index_file("../outside.md", workspace=self.root)
        self.assertEqual(self.memory.items, [])

    def test_rejects_non_regular_file(self):
        (self.root / "folder.md").mkdir()
        with self.assertRaises((ValueError, OSError)):
            self.indexer.index_file("folder.md", workspace=self.root)
        self.assertEqual(self.memory.items, [])

    def test_rejects_oversized_file(self):
        (self.root / "large.md").write_bytes(b"x" * (KnowledgeIndexer.MAX_FILE_BYTES + 1))
        with self.assertRaises(ValueError):
            self.indexer.index_file("large.md", workspace=self.root)
        self.assertEqual(self.memory.items, [])


if __name__ == "__main__":
    unittest.main()
