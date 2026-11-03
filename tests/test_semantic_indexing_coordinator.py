import tempfile
import unittest
from pathlib import Path

from knowledge.engine import KnowledgeEngine
from knowledge.vector_store import VectorStore
from knowledge.indexing_coordinator import SemanticIndexCoordinator


class FakeEmbedder:
    model_id = "test/local"

    def embed(self, texts):
        return [[float(len(text) % 7 + 1), 1.0, 0.5] for text in texts]


class FailingEmbedder(FakeEmbedder):
    def embed(self, texts):
        raise RuntimeError("simulated local model failure")


class SemanticIndexCoordinatorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.engine = KnowledgeEngine(self.root / "knowledge.sqlite3")
        self.vectors = VectorStore(self.root / "vectors.sqlite3")
        self.embedder = FakeEmbedder()
        self.coordinator = SemanticIndexCoordinator(
            self.engine, self.vectors, self.embedder, batch_size=2
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_indexes_file_and_returns_source_attributed_vectors(self):
        source = self.workspace / "guide.md"
        source.write_text("Yuki local intelligence and retrieval.", encoding="utf-8")
        result = self.coordinator.index_file(source, workspace=self.workspace)
        self.assertEqual(result["vectors_indexed"], result["chunks_indexed"])
        chunks = self.engine.get_source_chunks(result["source_id"])
        matches = self.vectors.search(
            self.embedder.embed(["intelligence"])[0],
            model_id=self.embedder.model_id,
        )
        self.assertEqual({item["chunk_id"] for item in matches},
                         {chunk.chunk_id for chunk in chunks})

    def test_reindex_prunes_stale_chunk_vectors(self):
        source = self.workspace / "guide.md"
        source.write_text("first " * 900, encoding="utf-8")
        first = self.coordinator.index_file(source, workspace=self.workspace)
        old_ids = {item["chunk_id"] for item in self.vectors.search(
            [1, 0, 0], model_id=self.embedder.model_id, limit=100
        )}
        self.assertTrue(old_ids)
        source.write_text("replacement content only.", encoding="utf-8")
        second = self.coordinator.index_file(source, workspace=self.workspace)
        current_ids = {item.chunk_id for item in self.engine.get_source_chunks(second["source_id"])}
        stored_ids = {item["chunk_id"] for item in self.vectors.search(
            [1, 0, 0], model_id=self.embedder.model_id, limit=100
        )}
        self.assertEqual(stored_ids, current_ids)
        self.assertGreater(second["stale_vectors_removed"], 0)

    def test_removing_source_removes_vectors(self):
        source = self.workspace / "guide.md"
        source.write_text("remove this local knowledge source", encoding="utf-8")
        result = self.coordinator.index_file(source, workspace=self.workspace)
        self.assertTrue(self.coordinator.remove_source(result["source_id"]))
        self.assertEqual(self.vectors.search([1, 0, 0], model_id=self.embedder.model_id), [])
        self.assertEqual(self.engine.list_sources(), [])

    def test_embedding_failure_does_not_claim_index_success(self):
        source = self.workspace / "guide.md"
        source.write_text("local source content", encoding="utf-8")
        coordinator = SemanticIndexCoordinator(
            self.engine, self.vectors, FailingEmbedder()
        )
        with self.assertRaisesRegex(RuntimeError, "simulated"):
            coordinator.index_file(source, workspace=self.workspace)


if __name__ == "__main__":
    unittest.main()
