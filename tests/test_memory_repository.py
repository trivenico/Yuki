import json
import tempfile
import unittest
from pathlib import Path

from core.memory_repository import MemoryRepository
from core.memory_migration import MemoryMigrationError, migrate_memory


class MemoryRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_repository_add_list_search_delete(self):
        repo = MemoryRepository(self.root / "memory.json")
        note = repo.remember("Prefer concise deployment reports", category="preference")
        self.assertEqual(repo.list()[0]["id"], note["id"])
        self.assertEqual(repo.search("deployment reports")[0]["id"], note["id"])
        self.assertTrue(repo.delete(note["id"]))
        self.assertEqual(repo.list(), [])

    def test_clear_requires_explicit_confirmation(self):
        repo = MemoryRepository(self.root / "memory.json")
        repo.remember("keep this until confirmed")
        with self.assertRaises(PermissionError):
            repo.clear()
        self.assertEqual(len(repo.list()), 1)
        self.assertEqual(repo.clear(confirmed=True), 1)
        self.assertEqual(repo.list(), [])

    def test_corrupt_repository_fails_closed_without_overwriting(self):
        path = self.root / "memory.json"
        path.write_text("{broken", encoding="utf-8")
        repo = MemoryRepository(path)
        with self.assertRaises(RuntimeError):
            repo.remember("must not overwrite corrupted data")
        self.assertEqual(path.read_text(encoding="utf-8"), "{broken")

    def test_migration_preview_does_not_write_or_mutate_source(self):
        source = self.root / "legacy.json"
        destination = self.root / "unified.json"
        original = json.dumps(["first note", "second note"])
        source.write_text(original, encoding="utf-8")
        result = migrate_memory(source, destination)
        self.assertFalse(result["applied"])
        self.assertEqual(result["note_count"], 2)
        self.assertFalse(destination.exists())
        self.assertEqual(source.read_text(encoding="utf-8"), original)

    def test_migration_apply_preserves_source_and_creates_backup(self):
        source = self.root / "legacy.json"
        destination = self.root / "unified.json"
        source.write_text(json.dumps(["a note", {"text": "remember this", "category": "lesson"}]), encoding="utf-8")
        result = migrate_memory(source, destination, apply=True)
        self.assertTrue(result["applied"])
        self.assertEqual(Path(result["backup"]).read_text(encoding="utf-8"), source.read_text(encoding="utf-8"))
        entries = json.loads(destination.read_text(encoding="utf-8"))
        self.assertEqual([e["text"] for e in entries], ["a note", "remember this"])
        self.assertEqual(entries[1]["category"], "lesson")

    def test_migration_refuses_existing_destination_and_bad_source(self):
        source = self.root / "legacy.json"
        destination = self.root / "unified.json"
        source.write_text(json.dumps(["a note"]), encoding="utf-8")
        destination.write_text("keep me", encoding="utf-8")
        with self.assertRaises(MemoryMigrationError):
            migrate_memory(source, destination, apply=True)
        self.assertEqual(destination.read_text(encoding="utf-8"), "keep me")
        destination.unlink()
        source.write_text("{broken", encoding="utf-8")
        with self.assertRaises(MemoryMigrationError):
            migrate_memory(source, destination, apply=True)
        self.assertFalse(destination.exists())


if __name__ == "__main__":
    unittest.main()
