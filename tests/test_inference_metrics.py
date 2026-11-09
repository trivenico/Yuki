import sqlite3
import tempfile
import unittest
from pathlib import Path

from evaluation.inference_metrics import InferenceMetricsStore


DIGEST = "a" * 64


class InferenceMetricsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = InferenceMetricsStore(
            str(Path(self.tmp.name) / "metrics.sqlite3"),
            authorize=lambda actor, action: action in {
                "inference_metrics:write", "inference_metrics:read"
            },
        )

    def tearDown(self):
        self.tmp.cleanup()

    def record(self, **overrides):
        values = dict(
            actor_id="owner-1", task_id="task-1", model_id="qwen:4b",
            model_digest=DIGEST, quantization="Q4_K_M",
            runtime_name="ollama", runtime_version="0.5.0",
            hardware_profile="cpu-8core-16gib", outcome="succeeded",
            load_duration_ms=800, time_to_first_token_ms=120,
            prompt_tokens=100, generated_tokens=50, generation_duration_ms=1000,
            host_memory_before_bytes=8_000_000_000,
            host_memory_after_bytes=9_000_000_000,
        )
        values.update(overrides)
        return self.store.record(**values)

    def test_record_stores_measurements_without_prompt_or_output_fields(self):
        item = self.record()
        self.assertEqual(item["generated_tokens"], 50)
        self.assertNotIn("prompt", item)
        self.assertNotIn("output", item)
        self.assertNotIn("endpoint", item)

    def test_summary_reports_latency_and_throughput(self):
        self.record(task_id="task-a")
        self.record(task_id="task-b", generation_duration_ms=2000, generated_tokens=100,
                    time_to_first_token_ms=250)
        result = self.store.summary(actor_id="owner-1", model_digest=DIGEST)
        self.assertEqual(result["sample_count"], 2)
        self.assertEqual(result["p50_generation_duration_ms"], 1000)
        self.assertEqual(result["p95_generation_duration_ms"], 2000)
        self.assertEqual(result["p50_time_to_first_token_ms"], 120)
        self.assertEqual(result["mean_generated_tokens_per_second"], 50.0)
        self.assertFalse(result["automatic_routing_changed"])

    def test_task_record_is_append_only(self):
        self.record()
        with self.assertRaises(ValueError):
            self.record()

    def test_database_rejects_direct_update_and_delete(self):
        self.record()
        # sqlite3.Connection's context manager commits/rolls back but does not
        # close the connection. Close explicitly so Windows can remove the DB.
        db = sqlite3.connect(Path(self.tmp.name) / "metrics.sqlite3")
        try:
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("UPDATE inference_metrics SET outcome='failed'")
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("DELETE FROM inference_metrics")
        finally:
            db.close()

    def test_actor_authorization_is_required_for_read_and_write(self):
        denied = InferenceMetricsStore(
            str(Path(self.tmp.name) / "denied.sqlite3"),
            authorize=lambda actor, action: False,
        )
        with self.assertRaises(PermissionError):
            denied.record(
                actor_id="owner-1", task_id="task-1", model_id="m",
                model_digest=DIGEST, runtime_name="r", runtime_version="1",
                hardware_profile="h", outcome="succeeded",
            )
        with self.assertRaises(PermissionError):
            denied.summary(actor_id="owner-1", model_digest=DIGEST)

    def test_invalid_metrics_are_rejected(self):
        for overrides in (
            {"generation_duration_ms": -1},
            {"prompt_tokens": True},
            {"model_digest": "not-a-digest"},
            {"time_to_first_token_ms": 2000},
            {"host_memory_before_bytes": -1},
            {"quantization": "bad quantization"},
        ):
            with self.subTest(overrides=overrides):
                with self.assertRaises(ValueError):
                    self.record(**overrides)

    def test_url_safe_task_identifiers_are_accepted(self):
        for index, task_id in enumerate(("-workflow-id", "_workflow-id")):
            self.record(task_id=task_id, model_id=f"model-{index}")

    def test_failed_and_cancelled_runs_are_counted(self):
        self.record(task_id="failed", outcome="failed")
        self.record(task_id="cancelled", outcome="cancelled")
        result = self.store.summary(actor_id="owner-1", model_digest=DIGEST)
        self.assertEqual(result["failure_count"], 1)
        self.assertEqual(result["sample_count"], 2)

    def test_metrics_are_scoped_by_actor_and_model_digest(self):
        self.record()
        other = self.store.summary(actor_id="other-owner", model_digest=DIGEST)
        self.assertEqual(other["sample_count"], 0)


if __name__ == "__main__":
    unittest.main()
