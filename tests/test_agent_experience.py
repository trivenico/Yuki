import tempfile
import unittest
from pathlib import Path

from evaluation.agent_experience import AgentExperienceStore


class AgentExperienceStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.grants = {"owner": {"agent_experience:write", "agent_experience:read"}}
        self.store = AgentExperienceStore(
            str(Path(self.temp.name) / "experience.sqlite"),
            authorize=lambda actor, action, agent: action in self.grants.get(actor, set()),
        )

    def record(self, **overrides):
        values = dict(actor_id="owner", agent_id="research", task_id="task-1",
                      outcome="succeeded", duration_ms=1200, evaluator="human-review",
                      evidence_ref="task-event:task-1", quality_score=0.9,
                      memory_useful=True)
        values.update(overrides)
        return self.store.record(**values)

    def test_records_bounded_metadata_without_prompt_or_output_fields(self):
        item = self.record()
        self.assertEqual(item["outcome"], "succeeded")
        self.assertNotIn("prompt", item)
        self.assertNotIn("model_output", item)

    def test_summary_is_scoped_and_reports_aggregates(self):
        self.record()
        self.record(task_id="task-2", outcome="failed", quality_score=0.2,
                    duration_ms=800, memory_useful=False)
        summary = self.store.summary(actor_id="owner", agent_id="research")
        self.assertEqual(summary["sample_count"], 2)
        self.assertEqual(summary["success_count"], 1)
        self.assertEqual(summary["failure_count"], 1)
        self.assertEqual(summary["mean_quality_score"], 0.55)
        self.assertFalse(summary["routing_changed"])
        self.assertFalse(summary["memory_written"])

    def test_duplicate_task_record_is_rejected(self):
        self.record()
        with self.assertRaisesRegex(ValueError, "already_recorded"):
            self.record()

    def test_write_and_read_permissions_are_enforced(self):
        self.grants["owner"].clear()
        with self.assertRaises(PermissionError):
            self.record()
        with self.assertRaises(PermissionError):
            self.store.summary(actor_id="owner", agent_id="research")

    def test_invalid_scores_and_outcomes_are_rejected(self):
        with self.assertRaises(ValueError):
            self.record(quality_score=1.1)
        with self.assertRaises(ValueError):
            self.record(outcome="unknown")

    def test_url_safe_task_ids_are_accepted(self):
        item = self.record(task_id="_task-url-safe")
        self.assertEqual(item["task_id"], "_task-url-safe")

    def test_memory_usefulness_is_not_inferred(self):
        item = self.record(memory_useful=None)
        self.assertIsNone(item["memory_useful"])


if __name__ == "__main__":
    unittest.main()
