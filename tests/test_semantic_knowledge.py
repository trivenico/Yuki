import tempfile
import unittest
from pathlib import Path

from knowledge.embeddings import SentenceTransformerEmbedder
from knowledge.vector_store import VectorStore
from knowledge.hybrid_search import HybridKnowledgeSearch
from knowledge.engine import KnowledgeEngine


class FakeModel:
    def encode(self, texts, **kwargs):
        return [[len(text), 1.0, 0.5] for text in texts]


class EmbeddingTests(unittest.TestCase):
    def test_normalizes_local_model_vectors(self):
        backend = SentenceTransformerEmbedder(FakeModel(), model_id="local/test")
        vectors = backend.embed(["hello", "world"])
        self.assertEqual(len(vectors), 2)
        self.assertAlmostEqual(sum(x*x for x in vectors[0]), 1.0)
        self.assertEqual(backend.model_id, "local/test")

    def test_rejects_invalid_embedding_input_and_model_output(self):
        backend = SentenceTransformerEmbedder(FakeModel(), model_id="local/test")
        for texts in ([], [""], ["x" * 20001], ["x"] * 257):
            with self.subTest(length=len(texts)), self.assertRaises(ValueError):
                backend.embed(texts)


class VectorStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = VectorStore(Path(self.temp.name) / "vectors.sqlite3")

    def tearDown(self):
        self.temp.cleanup()

    def test_upsert_search_and_model_isolation(self):
        self.store.upsert_many([
            {"chunk_id": 1, "source_id": "s1", "vector": [1.0, 0.0]},
            {"chunk_id": 2, "source_id": "s1", "vector": [0.0, 1.0]},
        ], model_id="model-a")
        self.store.upsert(chunk_id=3, source_id="s2", model_id="model-b", vector=[1.0, 0.0])
        results = self.store.search([0.9, 0.1], model_id="model-a")
        self.assertEqual(results[0]["chunk_id"], 1)
        self.assertEqual(len(self.store.search([1.0, 0.0], model_id="model-b")), 1)

    def test_upsert_replaces_and_delete_source_removes_vectors(self):
        self.store.upsert(chunk_id=1, source_id="s1", model_id="m", vector=[1, 0])
        self.store.upsert(chunk_id=1, source_id="s1", model_id="m", vector=[0, 1])
        self.assertEqual(self.store.search([0, 1], model_id="m")[0]["score"], 1.0)
        self.assertEqual(self.store.delete_source("s1"), 1)
        self.assertEqual(self.store.search([0, 1], model_id="m"), [])

    def test_rejects_invalid_vectors_and_limits(self):
        for vector in ([], [0, 0], [float("nan"), 1], [float("inf"), 1]):
            with self.subTest(vector=vector), self.assertRaises(ValueError):
                self.store.upsert(chunk_id=1, source_id="s", model_id="m", vector=vector)
        with self.assertRaises(ValueError):
            self.store.search([1, 0], model_id="m", limit=True)


class HybridSearchTests(unittest.TestCase):
    def test_fuses_lexical_and_semantic_candidates_with_canonical_provenance(self):
        class Engine:
            def search(self, query, *, limit):
                return [type("R", (), {
                    "chunk_id": 1, "source_id": "s1", "source_name": "a.md",
                    "source_path": "/a.md", "content": "lexical result"
                })()]
            def get_chunks(self, ids):
                return [type("R", (), {
                    "chunk_id": 2, "source_id": "s2", "source_name": "b.md",
                    "source_path": "/b.md", "content": "canonical semantic result"
                })()] if 2 in ids else []

        class Store:
            def search(self, vector, *, model_id, limit):
                return [{"chunk_id": 2, "source_id": "s2", "score": 0.9},
                        {"chunk_id": 999, "source_id": "attacker", "score": 1.0}]

        class Backend:
            model_id = "local/test"
            def embed(self, texts):
                return [[1.0, 0.0]]

        result = HybridKnowledgeSearch(Engine(), Store(), Backend()).search("meaning")
        self.assertEqual({item["chunk_id"] for item in result}, {1, 2})
        semantic = next(item for item in result if item["chunk_id"] == 2)
        self.assertEqual(semantic["content"], "canonical semantic result")
        self.assertEqual(semantic["source_id"], "s2")


class EngineChunkResolutionTests(unittest.TestCase):
    def test_get_chunks_only_returns_current_canonical_chunks(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "workspace"
            root.mkdir()
            source = root / "doc.md"
            source.write_text("Yuki local knowledge engine.", encoding="utf-8")
            engine = KnowledgeEngine(Path(temp) / "knowledge.sqlite3")
            engine.ingest_file(source, workspace=root)
            results = engine.search("local knowledge")
            resolved = engine.get_chunks([results[0].chunk_id, 999999])
            self.assertEqual(len(resolved), 1)
            self.assertEqual(resolved[0].source_id, results[0].source_id)


if __name__ == "__main__":
    unittest.main()
