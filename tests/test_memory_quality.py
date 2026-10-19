import tempfile
import unittest
from pathlib import Path

from companion.memory import MemoryStore
from companion.proactive import ReminderQueue
from core.vector_memory import VectorMemory
from skills.base import Skill, SkillResult
from skills.registry import SkillRegistry


class InvalidSkill(Skill):
    name = "invalid"
    description = ""  # Empty description!
    risk = "low"

    def run(self, argument=""):
        return SkillResult(True, "ok")


class MemoryQualityTests(unittest.TestCase):
    def test_cjk_tokenization_and_vector_memory(self):
        with tempfile.TemporaryDirectory() as tmp:
            mem_path = Path(tmp) / "memory.json"
            vec = VectorMemory(mem_path)
            vec.remember("Yuki 支持 中文 和 English", category="lesson")
            results = vec.search("中文")
            self.assertEqual(len(results), 1)

    def test_memory_doctor_repairs_corrupt_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            mem_file = Path(tmp) / "memory.json"
            mem_file.write_text("corrupt json data", encoding="utf-8")
            store = MemoryStore(mem_file)
            res = store.doctor()
            self.assertEqual(res["status"], "repaired")
            self.assertTrue((Path(tmp) / "memory.json.bak").exists())

    def test_reminders_purge_removes_delivered_items(self):
        with tempfile.TemporaryDirectory() as tmp:
            rem_file = Path(tmp) / "reminders.json"
            queue = ReminderQueue(rem_file)
            from datetime import datetime, timedelta, timezone
            queue.add("Future", datetime.now(timezone.utc) + timedelta(days=1))
            queue.add("Past", datetime.now(timezone.utc) - timedelta(days=1))
            queue.due()  # delivers "Past"

            purged = queue.purge()
            self.assertEqual(purged, 1)
            self.assertEqual(len(queue.list()), 1)

    def test_registry_validation_rejects_empty_description(self):
        registry = SkillRegistry()
        with self.assertRaises(ValueError):
            registry.register(InvalidSkill())


if __name__ == "__main__":
    unittest.main()
