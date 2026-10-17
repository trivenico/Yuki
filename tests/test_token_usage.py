import tempfile
import unittest
from pathlib import Path
from core.token_usage import TokenUsageTracker

class TokenUsageTests(unittest.TestCase):
    def test_records_exact_and_estimated_usage_separately(self):
        with tempfile.TemporaryDirectory() as d:
            tracker = TokenUsageTracker(Path(d) / "usage.json", session_id="s1", alert_threshold=100)
            first = tracker.record(provider="ollama", model="local-test", input_tokens=10, output_tokens=5)
            self.assertEqual(first["summary"]["total_tokens"], 15)
            tracker.record(provider="ollama", model="local-test", input_tokens=20, output_tokens=10, estimated=True)
            summary = tracker.summary()
            self.assertEqual(summary["exact_events"], 1)
            self.assertEqual(summary["estimated_events"], 1)
            self.assertEqual(summary["total_tokens"], 45)

    def test_alerts_on_threshold_and_spike(self):
        tracker = TokenUsageTracker(session_id="s", alert_threshold=50, spike_multiplier=2)
        tracker.record(provider="ollama", input_tokens=5, output_tokens=5)
        second = tracker.record(provider="ollama", input_tokens=30, output_tokens=30)
        self.assertIn("usage_spike", second["summary"]["alerts"])
        self.assertIn("session_token_threshold", second["summary"]["alerts"])

    def test_rejects_invalid_counts_and_boolean_as_int(self):
        tracker = TokenUsageTracker()
        with self.assertRaises(ValueError):
            tracker.record(provider="ollama", input_tokens=True, output_tokens=1)
        with self.assertRaises(ValueError):
            tracker.record(provider="ollama", input_tokens=-1, output_tokens=1)

    def test_persists_and_loads(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "usage.json"
            TokenUsageTracker(path, session_id="persist").record(provider="ollama", input_tokens=2, output_tokens=3)
            loaded = TokenUsageTracker(path, session_id="persist")
            self.assertEqual(loaded.summary()["total_tokens"], 5)

if __name__ == "__main__":
    unittest.main()
