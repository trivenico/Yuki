import tempfile
import unittest
from pathlib import Path

from core.token_usage import TokenUsageTracker


class ObservabilityTests(unittest.TestCase):
    def test_token_usage_tracker_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            tracker_file = Path(tmp) / "token_usage.json"
            tracker = TokenUsageTracker(tracker_file, session_id="test_sess")
            tracker.record(provider="ollama", model="llama3.2", input_tokens=100, output_tokens=50)

            summary = tracker.summary()
            self.assertEqual(summary["total_tokens"], 150)
            self.assertEqual(summary["calls"], 1)


if __name__ == "__main__":
    unittest.main()
