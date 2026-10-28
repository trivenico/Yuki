import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from companion.evolution import DailyEvolutionCycle, redact_secrets


class DailyEvolutionTests(unittest.TestCase):
    def test_secret_like_values_are_redacted_before_persistence(self):
        text = "Worked with api_key=super-secret-value and token: abc123"
        cleaned = redact_secrets(text)
        self.assertNotIn("super-secret-value", cleaned)
        self.assertNotIn("abc123", cleaned)
        self.assertIn("[REDACTED]", cleaned)

    def test_cycle_uses_recent_explicit_observations_and_does_not_execute_actions(self):
        with tempfile.TemporaryDirectory() as directory:
            cycle = DailyEvolutionCycle(directory)
            now = datetime(2026, 10, 17, 3, tzinfo=timezone.utc)
            cycle.record("failure", "deployment test failed twice", occurred_at=now - timedelta(days=1))
            cycle.record("feedback", "please keep reports concise", occurred_at=now)
            cycle.record("lesson", "deployment tests should run first", occurred_at=now - timedelta(days=40))
            report = cycle.run_cycle(now=now, lookback_days=7)
            self.assertEqual(report["observations_reviewed"], 2)
            self.assertEqual(report["summary"]["failures"], 1)
            self.assertEqual(report["summary"]["feedback"], 1)
            self.assertTrue(report["safety"]["local_only"])
            self.assertFalse(report["safety"]["actions_executed"])
            self.assertFalse(report["safety"]["code_or_config_modified"])
            self.assertTrue(all(p["status"] == "pending_review" for p in report["proposals"]))

    def test_proposal_review_records_decision_once_without_applying_it(self):
        with tempfile.TemporaryDirectory() as directory:
            cycle = DailyEvolutionCycle(directory)
            now = datetime(2026, 10, 17, 3, tzinfo=timezone.utc)
            cycle.record("failure", "build failed", occurred_at=now)
            report = cycle.run_cycle(now=now)
            proposal_id = report["proposals"][0]["id"]
            result = cycle.review(proposal_id, "accepted_for_manual_followup")
            self.assertEqual(result["status"], "accepted_for_manual_followup")
            with self.assertRaises(ValueError):
                cycle.review(proposal_id, "rejected")

    def test_report_is_read_only_and_returns_latest_proposals(self):
        with tempfile.TemporaryDirectory() as directory:
            cycle = DailyEvolutionCycle(directory)
            self.assertIsNone(cycle.report())
            now = datetime(2026, 10, 17, 3, tzinfo=timezone.utc)
            cycle.record("failure", "build failed", occurred_at=now)
            generated = cycle.run_cycle(now=now)
            report_path = cycle.report_path
            before = report_path.read_text(encoding="utf-8")
            loaded = cycle.report()
            after = report_path.read_text(encoding="utf-8")
            self.assertEqual(loaded, generated)
            self.assertEqual(before, after)
            self.assertGreaterEqual(len(loaded["proposals"]), 1)

    def test_report_fails_closed_for_invalid_json(self):
        with tempfile.TemporaryDirectory() as directory:
            cycle = DailyEvolutionCycle(directory)
            cycle.report_path.write_text("{invalid", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "unreadable"):
                cycle.report()

    def test_cycle_rejects_naive_clock_and_invalid_lookback(self):
        with tempfile.TemporaryDirectory() as directory:
            cycle = DailyEvolutionCycle(directory)
            with self.assertRaises(ValueError):
                cycle.run_cycle(now=datetime(2026, 10, 17))
            with self.assertRaises(ValueError):
                cycle.run_cycle(lookback_days=0)

    def test_daily_scheduler_is_opt_in(self):
        from companion.evolution import main
        with self.assertRaises(SystemExit):
            main([])


if __name__ == "__main__":
    unittest.main()
