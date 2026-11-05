import unittest

from tools.provider_live_conformance import Check, LiveConformanceReport


class LiveConformanceReportTests(unittest.TestCase):
    def test_report_is_partial_when_capability_was_not_exercised(self):
        report = LiveConformanceReport(
            1, "ollama", "live",
            (Check("availability", "pass", "available"),
             Check("streaming_round_trip", "not_run", "native_streaming_not_advertised")),
            0.5, 12,
        )
        self.assertEqual(report.status, "partial")
        self.assertEqual(report.to_dict()["status"], "partial")

    def test_report_fails_when_any_live_check_fails(self):
        report = LiveConformanceReport(
            1, "ollama", "live",
            (Check("availability", "pass", "available"),
             Check("chat_round_trip", "fail", "live_inference_failed_TimeoutError")),
            0.5, 0,
        )
        self.assertEqual(report.status, "fail")

    def test_report_passes_only_when_every_check_ran_and_passed(self):
        report = LiveConformanceReport(
            1, "ollama", "live",
            (Check("availability", "pass", "available"),
             Check("chat_round_trip", "pass", "live_inference_returned_nonempty_text")),
            0.5, 10,
        )
        self.assertEqual(report.status, "pass")


if __name__ == "__main__":
    unittest.main()
