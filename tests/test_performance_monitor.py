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
        self.assertIn("Remaining model context: Unknown", rendered)
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


if __name__ == "__main__":
    unittest.main()
