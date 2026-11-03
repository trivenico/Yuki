import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from core.episodic_memory import EpisodicMemoryService
from core.user_memory_service import UserMemoryService


class EpisodicMemoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.memory = UserMemoryService(root / "memory", root / "consent.sqlite")
        self.episodes = EpisodicMemoryService(root / "episodes.sqlite", self.memory)
        self.alice = SimpleNamespace(user_id="alice")
        self.bob = SimpleNamespace(user_id="bob")
        self.memory.set_enabled(self.alice, True)
        self.memory.set_enabled(self.bob, True)

    def tearDown(self):
        self.temp.cleanup()

    def record(self, user=None, ref="task-1", summary="Implemented local model evaluation."):
        return self.episodes.record(
            user or self.alice, task_ref=ref, task_type="engineering",
            summary=summary, outcome="Tests passed.", tags=["Yuki", "models"],
        )

    def test_records_distilled_episode_and_is_idempotent(self):
        first = self.record()
        again = self.record()
        self.assertEqual(first["id"], again["id"])
        self.assertEqual(first["category"], "episode")
        self.assertEqual(first["outcome"], "Tests passed.")

    def test_search_returns_relevant_episodes(self):
        self.record(ref="task-a", summary="Implemented local model evaluation.")
        self.record(ref="task-b", summary="Designed Japanese voice interaction.")
        results = self.episodes.search(self.alice, "local model benchmark", limit=5)
        self.assertEqual([item["task_ref"] for item in results], ["task-a"])

    def test_owner_isolation_for_search_list_and_delete(self):
        episode = self.record()
        self.assertEqual(self.episodes.list_recent(self.bob), [])
        self.assertEqual(self.episodes.search(self.bob, "local model"), [])
        self.assertFalse(self.episodes.forget(self.bob, episode["id"]))
        self.assertEqual(len(self.episodes.list_recent(self.alice)), 1)

    def test_consent_is_required_for_record_and_retrieval(self):
        self.memory.set_enabled(self.alice, False)
        with self.assertRaises(PermissionError):
            self.record()
        with self.assertRaises(PermissionError):
            self.episodes.search(self.alice, "anything")

    def test_secret_like_content_is_redacted(self):
        item = self.record(summary="Deploy uses api_key=supersecretvalue")
        self.assertNotIn("supersecretvalue", item["summary"])
        self.assertIn("[REDACTED]", item["summary"])

    def test_conflicting_task_reference_is_rejected(self):
        self.record()
        with self.assertRaises(ValueError):
            self.record(summary="Different summary.")

    def test_invalid_limits_and_tags_are_rejected(self):
        with self.assertRaises(ValueError):
            self.episodes.list_recent(self.alice, limit=True)
        with self.assertRaises(ValueError):
            self.episodes.search(self.alice, "x", limit=0)
        with self.assertRaises(ValueError):
            self.episodes.record(
                self.alice, task_ref="task-x", task_type="test",
                summary="Summary", outcome="Done", tags=["x"] * 13,
            )

    def test_explicit_delete_removes_only_owned_episode(self):
        episode = self.record()
        self.assertTrue(self.episodes.forget(self.alice, episode["id"]))
        self.assertFalse(self.episodes.forget(self.alice, episode["id"]))
        self.assertEqual(self.episodes.list_recent(self.alice), [])


if __name__ == "__main__":
    unittest.main()
