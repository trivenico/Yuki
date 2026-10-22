import unittest
from unittest.mock import patch

from core.performance_monitor import (collect_performance, format_bytes, format_performance_report,
                                     _reset_gpu_metrics_cache_for_tests,
                                     _reset_gpu_metrics_async_for_tests)


class PerformanceMonitorTests(unittest.TestCase):
    def setUp(self):
        _reset_gpu_metrics_cache_for_tests()
        _reset_gpu_metrics_async_for_tests()

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

        from core import performance_monitor
        with (
            patch("core.performance_monitor.shutil.which", return_value="/usr/bin/nvidia-smi"),
            patch("core.performance_monitor.subprocess.run", return_value=Result()),
        ):
            metrics = performance_monitor._probe_gpu_metrics()
        self.assertTrue(metrics["available"])
        self.assertEqual(len(metrics["devices"]), 1)
        self.assertEqual(metrics["devices"][0]["name"], "GPU B")
        self.assertEqual(metrics["devices"][0]["utilization_percent"], 45)

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

    def test_gpu_probe_is_cached_and_returns_detached_results(self):
        from core import performance_monitor

        class Result:
            returncode = 0
            stdout = "GPU A, 40, 100, 1000\n"

        with (
            patch("core.performance_monitor.shutil.which", return_value="/usr/bin/nvidia-smi"),
            patch("core.performance_monitor.subprocess.run", return_value=Result()) as run,
        ):
            first = performance_monitor._gpu_metrics()
            first["devices"][0]["name"] = "mutated"
            second = performance_monitor._gpu_metrics()
        self.assertEqual(run.call_count, 1)
        self.assertEqual(second["devices"][0]["name"], "GPU A")

    def test_gpu_probe_cache_expires(self):
        from core import performance_monitor

        class Result:
            returncode = 0
            stdout = "GPU A, 40, 100, 1000\n"

        with (
            patch("core.performance_monitor.time.monotonic", side_effect=[10.0, 10.1, 12.0, 16.0, 16.1]),
            patch("core.performance_monitor.shutil.which", return_value="/usr/bin/nvidia-smi"),
            patch("core.performance_monitor.subprocess.run", return_value=Result()) as run,
        ):
            performance_monitor._gpu_metrics()
            performance_monitor._gpu_metrics()
            performance_monitor._gpu_metrics()
        self.assertEqual(run.call_count, 2)

    def test_nonblocking_gpu_probe_returns_pending_without_waiting(self):
        from concurrent.futures import Future
        from core import performance_monitor

        pending = Future()
        with patch.object(performance_monitor._GPU_EXECUTOR, "submit", return_value=pending) as submit:
            first = performance_monitor._gpu_metrics_nonblocking()
        self.assertEqual(first["status"], "pending")
        self.assertFalse(first["available"])
        submit.assert_called_once_with(performance_monitor._probe_gpu_metrics)

    def test_nonblocking_gpu_probe_reports_completed_sample_and_freshness(self):
        from concurrent.futures import Future
        from core import performance_monitor

        completed = Future()
        completed.set_result({"available": True, "devices": [
            {"name": "GPU", "utilization_percent": 50, "memory_used_mib": 100,
             "memory_total_mib": 1000}
        ]})
        with patch.object(performance_monitor._GPU_EXECUTOR, "submit", return_value=completed):
            first = performance_monitor._gpu_metrics_nonblocking()
            second = performance_monitor._gpu_metrics_nonblocking()
        self.assertEqual(first["status"], "pending")
        self.assertEqual(second["status"], "fresh")
        second["devices"][0]["name"] = "changed"
        self.assertEqual(performance_monitor._gpu_metrics_nonblocking()["devices"][0]["name"], "GPU")

    def test_performance_collection_uses_nonblocking_gpu_reader(self):
        from core import performance_monitor

        with patch.object(performance_monitor, "_gpu_metrics_nonblocking",
                          return_value={"available": False, "status": "pending"}) as reader, \\
             patch.object(performance_monitor, "_gpu_metrics",
                          side_effect=AssertionError("synchronous GPU reader called")):
            metrics = collect_performance()
        reader.assert_called_once_with()
        self.assertEqual(metrics["gpu"]["status"], "pending")


if __name__ == "__main__":
    unittest.main()
