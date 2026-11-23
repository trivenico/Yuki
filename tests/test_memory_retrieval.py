import tempfile
import unittest
from pathlib import Path

from companion.memory import MemoryStore


class MemoryRetrievalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = MemoryStore(Path(self.temp.name) / "memory.json")

    def tearDown(self):
        self.temp.cleanup()

    def test_retrieves_active_relevant_memories_and_tracks_usage(self):
        relevant = self.store.add("Use bounded retries for worker failures.", category="lesson")
        unrelated = self.store.add("The preferred editor is VS Code.", category="preference")
        results = self.store.retrieve("worker retry failures")
        self.assertEqual([item["id"] for item in results], [relevant["id"]])
        self.assertEqual(results[0]["use_count"], 1)
        self.assertIsNotNone(results[0]["last_used_at"])
        self.assertEqual(self.store.list(limit=2)[0]["use_count"], 0)

    def test_archived_memories_are_not_retrieved(self):
        saved = self.store.add("Archived worker recovery procedure.", category="lesson")
        self.store.update_lifecycle(
            saved["id"], expected_version=1, status="archived",
            reason="Replaced by a current procedure.",
        )
        self.assertEqual(self.store.retrieve("worker recovery"), [])

    def test_confidence_adjusts_ranking_after_relevance(self):
        lower = self.store.add("Worker retry policy for failed tasks.", category="lesson")
        higher = self.store.add("Worker retry policy for failed tasks.", category="lesson")
        self.store.update_lifecycle(
            higher["id"], expected_version=1, confidence=0.95,
            reason="Reviewed against verified incidents.",
        )
        results = self.store.retrieve("worker retry policy")
        self.assertEqual(results[0]["id"], higher["id"])
        self.assertEqual(results[1]["id"], lower["id"])

    def test_rejects_invalid_query_and_limit(self):
        with self.assertRaises(ValueError):
            self.store.retrieve(" ")
        with self.assertRaises(ValueError):
            self.store.retrieve("worker", limit=0)


if __name__ == "__main__":
    unittest.main()
