import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path

from research.local_worker import LocalResearchWorker


@dataclass
class Match:
    chunk_id: int
    source_id: str
    source_name: str
    source_path: str
    content: str
    score: float


class FakePlanner:
    def __init__(self, plan):
        self.plan = plan

    def get_plan(self, plan_id):
        if plan_id != self.plan["plan_id"]:
            raise KeyError("missing plan")
        return self.plan


class FakeKnowledge:
    def __init__(self, matches=None):
        self.matches = matches or {}
        self.calls = []

    def search(self, query, *, limit):
        self.calls.append((query, limit))
        return self.matches.get(query, [])[:limit]


class LocalResearchWorkerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.plan = {
            "plan_id": "plan-1", "goal": "Research Yuki memory",
            "questions": ["local memory", "evidence ledger"], "status": "draft",
        }
        self.knowledge = FakeKnowledge({
            "local memory": [
                Match(1, "source-a", "memory.md", "/docs/memory.md", "Local memory notes", -1.0),
                Match(2, "source-a", "memory.md", "/docs/memory.md", "Memory persistence", -0.8),
            ],
            "evidence ledger": [
                Match(2, "source-a", "memory.md", "/docs/memory.md", "Memory persistence", -0.8),
                Match(3, "source-b", "ledger.md", "/docs/ledger.md", "Evidence review", -0.7),
            ],
        })
        self.worker = LocalResearchWorker(
            FakePlanner(self.plan), self.knowledge,
            Path(self.temp.name) / "runs.sqlite3",
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_retrieves_local_results_with_source_provenance(self):
        result = self.worker.run_local_retrieval("plan-1")
        self.assertEqual(result["mode"], "local_read_only")
        self.assertEqual(result["summary"]["unique_chunks"], 3)
        self.assertEqual(result["summary"]["unique_sources"], 2)
        self.assertEqual(result["evidence"][0]["source_id"], "source-a")
        self.assertEqual(result["queries"][0]["status"], "evidence_found")
        self.assertEqual(len(self.knowledge.calls), 2)

    def test_run_is_persisted_and_idempotent(self):
        first = self.worker.run_local_retrieval("plan-1")
        second = self.worker.run_local_retrieval("plan-1")
        self.assertEqual(first["run_id"], second["run_id"])
        self.assertFalse(first["reused"])
        self.assertTrue(second["reused"])
        self.assertEqual(self.worker.get_run(first["run_id"])["goal"], self.plan["goal"])
        self.assertEqual(len(self.worker.list_runs("plan-1")), 1)

    def test_no_match_is_not_reported_as_absence_of_knowledge(self):
        self.knowledge.matches = {"local memory": [], "evidence ledger": []}
        result = self.worker.run_local_retrieval("plan-1")
        self.assertEqual(result["summary"]["unanswered_questions"],
                         ["local memory", "evidence ledger"])
        self.assertTrue(any("does not establish" in item for item in result["limitations"]))

    def test_cancelled_plan_is_not_executed(self):
        self.plan["status"] = "cancelled"
        with self.assertRaisesRegex(ValueError, "Cancelled"):
            self.worker.run_local_retrieval("plan-1")
        self.assertEqual(self.knowledge.calls, [])

    def test_invalid_query_limit_is_rejected(self):
        for value in (0, 6, True, 1.5):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.worker.run_local_retrieval("plan-1", per_question=value)

    def test_missing_plan_and_invalid_plan_are_rejected(self):
        with self.assertRaises(KeyError):
            self.worker.run_local_retrieval("missing")
        self.plan["questions"] = [" "]
        with self.assertRaisesRegex(ValueError, "non-empty"):
            self.worker.run_local_retrieval("plan-1")

    def test_result_budget_caps_unique_chunks(self):
        self.plan["questions"] = [f"question {i}" for i in range(8)]
        self.knowledge.matches = {
            q: [Match(i + 1, f"s{i}", f"f{i}", f"/f{i}", "content", -1.0)
                for i in range(10)]
            for q in self.plan["questions"]
        }
        result = self.worker.run_local_retrieval("plan-1")
        self.assertLessEqual(result["summary"]["unique_chunks"], 40)


if __name__ == "__main__":
    unittest.main()
