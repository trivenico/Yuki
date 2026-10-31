import json
import tempfile
import unittest
from pathlib import Path

from companion.memory import MemoryStore


class MemoryStoreValidationTests(unittest.TestCase):
    def test_rejects_malformed_category_without_crashing_with_type_error(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "memory.json"
            path.write_text(json.dumps([{
                "id": "abc", "created_at": "2026-10-31T00:00:00Z",
                "category": [], "text": "remembered text",
            }]), encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "invalid entry"):
                MemoryStore(path).list()

    def test_rejects_entries_over_text_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "memory.json"
            path.write_text(json.dumps([{
                "id": "abc", "created_at": "2026-10-31T00:00:00Z",
                "category": "note", "text": "x" * 4001,
            }]), encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "invalid entry"):
                MemoryStore(path).list()

    def test_rejects_memory_file_over_size_limit_before_json_parsing(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "memory.json"
            path.write_text(" " * (MemoryStore.MAX_FILE_BYTES + 1), encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "10 MB safety limit"):
                MemoryStore(path).list()


if __name__ == "__main__":
    unittest.main()
