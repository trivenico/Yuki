import tempfile
import unittest
from pathlib import Path

from knowledge.engine import KnowledgeEngine


class KnowledgeEngineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.engine = KnowledgeEngine(self.root / "data" / "knowledge.sqlite3")

    def tearDown(self):
        self.temp.cleanup()

    def write(self, name="notes.md", content="Yuki is a local-first AI assistant."):
        path = self.workspace / name
        path.write_text(content, encoding="utf-8")
        return path

    def test_ingest_search_and_source_attribution(self):
        source = self.write(content=(
            "Yuki is a local-first AI assistant.\n"
            "It uses a private knowledge engine for retrieval."
        ))
        indexed = self.engine.ingest_file(source, workspace=self.workspace)
        self.assertEqual(indexed["chunks_indexed"], 1)
        results = self.engine.search("local-first knowledge", limit=3)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].source_name, "notes.md")
        self.assertEqual(results[0].source_id, indexed["source_id"])
        self.assertIn("private knowledge engine", results[0].content)

    def test_unchanged_ingestion_is_idempotent(self):
        source = self.write()
        first = self.engine.ingest_file(source, workspace=self.workspace)
        second = self.engine.ingest_file(source, workspace=self.workspace)
        self.assertFalse(first["unchanged"])
        self.assertTrue(second["unchanged"])
        self.assertEqual(first["source_id"], second["source_id"])
        self.assertEqual(len(self.engine.list_sources()), 1)

    def test_changed_source_replaces_old_chunks(self):
        source = self.write(content="Old terminology is obsolete.")
        self.engine.ingest_file(source, workspace=self.workspace)
        source.write_text("New terminology is current.", encoding="utf-8")
        self.engine.ingest_file(source, workspace=self.workspace)
        self.assertEqual(self.engine.search("obsolete"), [])
        self.assertEqual(len(self.engine.search("current")), 1)

    def test_remove_source_deletes_searchable_chunks(self):
        source = self.write()
        item = self.engine.ingest_file(source, workspace=self.workspace)
        self.assertTrue(self.engine.remove_source(item["source_id"]))
        self.assertFalse(self.engine.remove_source(item["source_id"]))
        self.assertEqual(self.engine.search("local-first"), [])
        self.assertEqual(self.engine.list_sources(), [])

    def test_rejects_paths_outside_workspace_and_symlinks(self):
        outside = self.root / "outside.md"
        outside.write_text("secret", encoding="utf-8")
        with self.assertRaises(ValueError):
            self.engine.ingest_file(outside, workspace=self.workspace)
        link = self.workspace / "linked.md"
        try:
            link.symlink_to(outside)
        except (OSError, NotImplementedError):
            self.skipTest("Symlinks unavailable on this platform")
        with self.assertRaises(ValueError):
            self.engine.ingest_file(link, workspace=self.workspace)

    def test_rejects_unsupported_large_empty_and_invalid_utf8(self):
        with self.assertRaises(ValueError):
            self.engine.ingest_file(self.write("image.png"), workspace=self.workspace)
        large = self.write("large.md", "x" * (self.engine.MAX_FILE_BYTES + 1))
        with self.assertRaises(ValueError):
            self.engine.ingest_file(large, workspace=self.workspace)
        empty = self.write("empty.md", "  ")
        with self.assertRaises(ValueError):
            self.engine.ingest_file(empty, workspace=self.workspace)
        invalid = self.workspace / "invalid.md"
        invalid.write_bytes(b"\xff\xfe")
        with self.assertRaises(ValueError):
            self.engine.ingest_file(invalid, workspace=self.workspace)

    def test_search_validation_and_fts_syntax_are_safe(self):
        self.write(content="Yuki retrieval engine supports local search.")
        self.engine.ingest_file(self.workspace / "notes.md", workspace=self.workspace)
        self.assertEqual(self.engine.search('"; DROP TABLE knowledge_sources; --'), [])
        self.assertEqual(len(self.engine.list_sources()), 1)
        for query, limit in (("", 5), ("valid", True), ("valid", 0), ("x" * 2001, 5)):
            with self.subTest(query=query[:10], limit=limit), self.assertRaises(ValueError):
                self.engine.search(query, limit=limit)

    def test_large_documents_are_chunked_with_overlap(self):
        text = "alpha " * 900
        source = self.write(content=text)
        indexed = self.engine.ingest_file(source, workspace=self.workspace)
        self.assertGreater(indexed["chunks_indexed"], 1)
        results = self.engine.search("alpha", limit=20)
        self.assertGreater(len(results), 1)
        self.assertTrue(all(len(item.content) <= self.engine.MAX_CHUNK_CHARS for item in results))
        self.assertTrue(all(item.source_id == indexed["source_id"] for item in results))

    def test_document_character_limit(self):
        source = self.write(content="x" * (self.engine.MAX_DOCUMENT_CHARS + 1))
        with self.assertRaises(ValueError):
            self.engine.ingest_file(source, workspace=self.workspace)


if __name__ == "__main__":
    unittest.main()
