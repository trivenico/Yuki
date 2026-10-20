"""Corruption and fail-closed persistence tests for local data stores."""

import json
import tempfile
import unittest
from pathlib import Path

from companion.memory import MemoryStore
from knowledge.knowledge_graph import KnowledgeGraph


class LocalDataIntegrityTests(unittest.TestCase):
    def test_memory_corrupt_json_is_not_overwritten_by_add(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "memory.json"
            path.write_text("{broken", encoding="utf-8")
            store = MemoryStore(path)
            with self.assertRaisesRegex(RuntimeError, "no data was overwritten"):
                store.add("new note")
            self.assertEqual(path.read_text(encoding="utf-8"), "{broken")

    def test_memory_invalid_structure_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "memory.json"
            path.write_text(json.dumps({"not": "a list"}), encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "invalid structure"):
                MemoryStore(path).list()
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"not": "a list"})

    def test_memory_invalid_entry_is_rejected_without_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "memory.json"
            original = [{"id": "one", "text": "missing created_at"}]
            path.write_text(json.dumps(original), encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "invalid entry"):
                MemoryStore(path).add("new note")
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), original)

    def test_graph_corrupt_json_is_not_overwritten_by_mutation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "graph.json"
            path.write_text("{broken", encoding="utf-8")
            graph = KnowledgeGraph(path)
            with self.assertRaisesRegex(RuntimeError, "unreadable"):
                graph.add_entity("new entity")
            self.assertEqual(path.read_text(encoding="utf-8"), "{broken")

    def test_graph_invalid_structure_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "graph.json"
            original = {"entities": [], "relations": {}}
            path.write_text(json.dumps(original), encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "invalid structure"):
                KnowledgeGraph(path).list_entities()
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), original)

    def test_graph_dangling_relation_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "graph.json"
            original = {
                "entities": [{"id": "a", "name": "A", "kind": "fact", "description": ""}],
                "relations": [{"id": "r", "source": "a", "target": "missing", "type": "links"}],
            }
            path.write_text(json.dumps(original), encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "missing entity"):
                KnowledgeGraph(path).list_entities()
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), original)


if __name__ == "__main__":
    unittest.main()
