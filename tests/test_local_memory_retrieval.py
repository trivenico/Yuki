import tempfile
import unittest
from pathlib import Path

from core.vector_memory import VectorMemory


class LocalMemoryRetrievalTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.memory = VectorMemory(Path(self.temp_dir.name) / "memory.json")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_retrieves_relevant_memories_and_ranks_rare_terms(self):
        self.memory.remember("Yuki uses a local model for private offline reasoning", category="lesson")
        self.memory.remember("The weather is pleasant today", category="note")
        self.memory.remember("Local model routing keeps offline inference private", category="preference")

        results = self.memory.search("local model offline")
        self.assertEqual(len(results), 2)
        self.assertTrue(all("score" in item for item in results))
        self.assertEqual({item["category"] for item in results}, {"lesson", "preference"})
        rare_term_results = self.memory.search("private reasoning")
        self.assertEqual(rare_term_results[0]["category"], "lesson")

    def test_supports_unicode_tokens(self):
        self.memory.remember("Yuki understands français and 日本語 with local models")
        results = self.memory.search("français 日本語")
        self.assertEqual(len(results), 1)
        self.assertIn("日本語", results[0]["text"])

    def test_category_filter_and_empty_token_query(self):
        self.memory.remember("prefers concise answers", category="preference")
        self.memory.remember("learned concise response formatting", category="lesson")
        results = self.memory.search("concise", category="preference")
        self.assertEqual([item["category"] for item in results], ["preference"])
        self.assertEqual(self.memory.search("!!!"), [])

    def test_explicit_forget_and_list(self):
        item = self.memory.remember("temporary note", category="note")
        self.assertEqual(len(self.memory.list()), 1)
        self.assertTrue(self.memory.forget(item["id"]))
        self.assertFalse(self.memory.forget(item["id"]))
        self.assertEqual(self.memory.list(), [])

    def test_validates_query_limit_and_category(self):
        for query in ("", " "):
            with self.subTest(query=query), self.assertRaises(ValueError):
                self.memory.search(query)
        with self.assertRaises(ValueError):
            self.memory.search("query", limit=0)
        with self.assertRaises(ValueError):
            self.memory.search("query", category="unknown")


if __name__ == "__main__":
    unittest.main()
