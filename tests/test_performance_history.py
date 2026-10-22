import unittest

from core.performance_history import PerformanceHistory, format_performance_history


class PerformanceHistoryTests(unittest.TestCase):
    def setUp(self):
        self.history = PerformanceHistory(max_samples=2)

    @staticmethod
    def metrics(cpu=10, tokens=20):
        return {
            "platform": "test",
            "cpu": {"percent": cpu},
            "memory": {"percent": 25, "used_bytes": 100, "total_bytes": 400},
            "gpu": {"available": True, "devices": [
                {"name": "private device name", "utilization_percent": 30,
                 "memory_used_mib": 200, "memory_total_mib": 1000}
            ]},
            "tokens": {"total_tokens": tokens, "input_tokens": 12, "output_tokens": 8},
            "context": {"remaining_tokens": 500},
            "prompt": "must never be retained",
            "provider_payload": {"secret": "must never be retained"},
        }

    def test_history_is_bounded_and_keeps_chronological_latest_samples(self):
        self.history.record(self.metrics(cpu=10))
        self.history.record(self.metrics(cpu=20))
        self.history.record(self.metrics(cpu=30))
        samples = self.history.snapshot()
        self.assertEqual(len(samples), 2)
        self.assertEqual([sample["cpu_percent"] for sample in samples], [20, 30])
        self.assertLessEqual(len(self.history), 2)

    def test_history_stores_only_allowlisted_numeric_metrics(self):
        sample = self.history.record(self.metrics())
        self.assertNotIn("prompt", sample)
        self.assertNotIn("provider_payload", sample)
        self.assertNotIn("name", sample["gpu"][0])
        self.assertEqual(sample["total_tokens"], 20)

    def test_snapshots_are_detached_from_internal_state(self):
        self.history.record(self.metrics())
        snapshot = self.history.snapshot()
        snapshot[0]["gpu"][0]["memory_used_mib"] = 999
        self.assertEqual(self.history.snapshot()[0]["gpu"][0]["memory_used_mib"], 200)

    def test_invalid_capacity_is_rejected(self):
        for invalid in (True, 0, -1, 10001, 2.0):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    PerformanceHistory(max_samples=invalid)

    def test_invalid_sample_and_limit_are_rejected(self):
        with self.assertRaises(TypeError):
            self.history.record(None)
        with self.assertRaises(ValueError):
            self.history.snapshot(limit=-1)

    def test_missing_or_invalid_measurements_remain_unknown(self):
        sample = self.history.record({"cpu": {"percent": 101}, "memory": {},
                                      "gpu": {}, "tokens": {}, "context": {}})
        self.assertIsNone(sample["cpu_percent"])
        self.assertIsNone(sample["ram_percent"])
        self.assertIsNone(sample["remaining_context_tokens"])

    def test_history_report_renders_unknown_metrics_without_zero_fallback(self):
        self.history.record({"cpu": {}, "memory": {}, "gpu": {}, "tokens": {}, "context": {}})
        report = format_performance_history(self.history)
        self.assertIn("CPU Unknown", report)
        self.assertIn("RAM Unknown", report)
        self.assertIn("GPU Unavailable", report)
        self.assertIn("latest-request context remaining Unknown", report)

    def test_history_report_handles_empty_buffer(self):
        self.assertIn("No performance history yet", format_performance_history(self.history))


if __name__ == "__main__":
    unittest.main()
