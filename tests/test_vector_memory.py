import tempfile
import unittest
from pathlib import Path

from core.vector_memory import VectorMemory


class VectorMemoryTests(unittest.TestCase):
    def test_lexical_fallback_without_embedding_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = VectorMemory(Path(tmp) / "memory.json")
            memory.remember("Yuki uses a local inference runtime")
            results = memory.search("local inference")
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0]["retrieval"], "lexical")

    def test_semantic_search_matches_different_wording(self):
        vectors = {
            "automobile": [1.0, 0.0],
            "I drive a car every day": [1.0, 0.0],
            "banana bread recipe": [0.0, 1.0],
        }
        with tempfile.TemporaryDirectory() as tmp:
            memory = VectorMemory(
                Path(tmp) / "memory.json",
                embedding_fn=lambda text: vectors.get(text, [0.0, 1.0]),
            )
            memory.remember("I drive a car every day")
            memory.remember("banana bread recipe")
            results = memory.search("automobile")
            self.assertEqual(results[0]["text"], "I drive a car every day")
            self.assertEqual(results[0]["retrieval"], "semantic")

    def test_embedding_failure_falls_back_to_lexical(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = VectorMemory(Path(tmp) / "memory.json", embedding_fn=lambda _: (_ for _ in ()).throw(RuntimeError()))
            memory.remember("Yuki model routing")
            results = memory.search("model routing")
            self.assertEqual(results[0]["retrieval"], "lexical")

    def test_invalid_vectors_do_not_crash_search(self):
        with tempfile.TemporaryDirectory() as tmp:
            memory = VectorMemory(Path(tmp) / "memory.json", embedding_fn=lambda _: [float("nan"), 0.0])
            memory.remember("Yuki model routing")
            self.assertEqual(memory.search("model routing")[0]["retrieval"], "lexical")

    def test_invalid_weight_and_limits_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                VectorMemory(Path(tmp) / "memory.json", semantic_weight=2)
            memory = VectorMemory(Path(tmp) / "memory.json")
            for limit in (0, 21, True):
                with self.assertRaises(ValueError):
                    memory.search("Yuki", limit=limit)


if __name__ == "__main__":
    unittest.main()
