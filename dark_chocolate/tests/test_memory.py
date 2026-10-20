import tempfile
import unittest
from pathlib import Path
from dark_chocolate.memory import KnowledgeGraph

class KnowledgeGraphTests(unittest.TestCase):
    def test_persistence_and_relation_query(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "graph.json"
            graph = KnowledgeGraph(path)
            graph.add_entity("host-1", "Host 1", "host")
            graph.add_entity("port-443", "HTTPS", "service")
            graph.add_relation("host-1", "exposes", "port-443")
            restored = KnowledgeGraph(path)
            self.assertEqual(len(restored.query_relations("port-443")), 1)
    def test_unknown_entity_cannot_be_related(self):
        with tempfile.TemporaryDirectory() as directory:
            graph = KnowledgeGraph(Path(directory) / "graph.json")
            with self.assertRaises(KeyError):
                graph.add_relation("missing", "relates", "also-missing")

if __name__ == "__main__":
    unittest.main()
