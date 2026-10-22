import unittest
from unittest.mock import patch

from core.performance_monitor import collect_performance, format_bytes, format_performance_report


class PerformanceMonitorTests(unittest.TestCase):
    def test_unknown_gpu_and_context_are_explicit(self):
        with patch("core.performance_monitor.shutil.which", return_value=None):
            metrics = collect_performance()
        self.assertFalse(metrics["gpu"]["available"])
        self.assertIsNone(metrics["context"]["remaining_tokens"])
        rendered = format_performance_report(metrics)
        self.assertIn("Remaining model context (latest request): Unknown", rendered)
        self.assertIn("Context scope: latest_request", rendered)
        self.assertIn("Tokens recorded: 0 total", rendered)

    def test_context_remaining_requires_known_valid_limit_and_usage(self):
        with patch("core.performance_monitor.shutil.which", return_value=None):
            metrics = collect_performance(context_limit=8192, context_used=2048)
        self.assertEqual(metrics["context"]["remaining_tokens"], 6144)

    def test_invalid_context_is_not_reported_as_remaining(self):
        with patch("core.performance_monitor.shutil.which", return_value=None):
            metrics = collect_performance(context_limit=100, context_used=101)
        self.assertIsNone(metrics["context"]["remaining_tokens"])

    def test_format_bytes_handles_unknown_and_values(self):
        self.assertEqual(format_bytes(None), "Unknown")
        self.assertEqual(format_bytes(1024), "1.0 KiB")

    def test_invalid_nvidia_sentinel_metrics_are_not_reported_as_real_gpu_data(self):
        class Result:
            returncode = 0
            stdout = "GPU A, -1, 100, 1000\nGPU B, 45, 200, 1000\nGPU C, 101, 10, 100\n"

        with (
            patch("core.performance_monitor.shutil.which", return_value="/usr/bin/nvidia-smi"),
            patch("core.performance_monitor.subprocess.run", return_value=Result()),
        ):
            metrics = collect_performance()
        self.assertTrue(metrics["gpu"]["available"])
        self.assertEqual(len(metrics["gpu"]["devices"]), 1)
        self.assertEqual(metrics["gpu"]["devices"][0]["name"], "GPU B")
        self.assertEqual(metrics["gpu"]["devices"][0]["utilization_percent"], 45)

    def test_malformed_token_summary_falls_back_to_explicit_unknown_summary(self):
        with patch("core.performance_monitor.shutil.which", return_value=None):
            metrics = collect_performance(token_summary="not-a-summary")
        self.assertIsInstance(metrics["tokens"], dict)
        self.assertIn("No valid token usage summary", metrics["tokens"]["detail"])

    def test_provider_context_budget_is_used_and_estimate_is_preserved(self):
        with patch("core.performance_monitor.shutil.which", return_value=None):
            metrics = collect_performance(context_budget_data={
                "limit_tokens": 8192, "used_tokens": 2048, "estimated": True,
                "remaining_tokens": 6144,
            })
        self.assertEqual(metrics["context"]["remaining_tokens"], 6144)
        self.assertTrue(metrics["context"]["estimated"])
        self.assertIn("estimated", metrics["context"]["detail"].lower())

    def test_malformed_provider_context_budget_remains_unknown(self):
        with patch("core.performance_monitor.shutil.which", return_value=None):
            metrics = collect_performance(context_budget_data={
                "limit_tokens": 8192, "used_tokens": 100, "estimated": "false",
            })
        self.assertFalse(metrics["context"]["available"])
        self.assertIsNone(metrics["context"]["remaining_tokens"])


if __name__ == "__main__":
    unittest.main()
