import json
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
            tracker.close()

    def test_alerts_are_advisory_and_never_block_recording(self):
        tracker = TokenUsageTracker(session_id="s", alert_threshold=50, spike_multiplier=2)
        tracker.record(provider="ollama", input_tokens=5, output_tokens=5)
        second = tracker.record(provider="ollama", input_tokens=30, output_tokens=30)
        self.assertIn("usage_spike", second["summary"]["alerts"])
        self.assertIn("session_token_threshold", second["summary"]["alerts"])
        third = tracker.record(provider="ollama", input_tokens=40, output_tokens=40)
        self.assertEqual(third["summary"]["calls"], 3)
        tracker.close()

    def test_no_artificial_event_count_limit(self):
        tracker = TokenUsageTracker(session_id="long-running")
        for _ in range(10_005):
            tracker.record(provider="ollama", input_tokens=1, output_tokens=1)
        summary = tracker.summary()
        self.assertEqual(summary["calls"], 10_005)
        self.assertEqual(summary["total_tokens"], 20_010)
        tracker.close()

    def test_accepts_event_larger_than_old_ten_million_ceiling(self):
        tracker = TokenUsageTracker(session_id="large-event")
        result = tracker.record(provider="local", input_tokens=10_000_001, output_tokens=10_000_002)
        self.assertEqual(result["summary"]["total_tokens"], 20_000_003)
        tracker.close()

    def test_cumulative_usage_exceeds_sqlite_signed_integer_without_overflow(self):
        tracker = TokenUsageTracker(session_id="large-total")
        per_event = (1 << 63) - 1
        tracker.record(provider="local", input_tokens=per_event, output_tokens=0)
        summary = tracker.record(provider="local", input_tokens=per_event, output_tokens=0)["summary"]
        self.assertEqual(summary["input_tokens"], per_event * 2)
        self.assertEqual(summary["total_tokens"], per_event * 2)
        tracker.close()

    def test_rejects_invalid_counts_and_boolean_as_int(self):
        tracker = TokenUsageTracker()
        with self.assertRaises(ValueError):
            tracker.record(provider="ollama", input_tokens=True, output_tokens=1)
        with self.assertRaises(ValueError):
            tracker.record(provider="ollama", input_tokens=-1, output_tokens=1)
        tracker.close()


    @unittest.skipUnless(__import__("os").name == "posix", "POSIX file permissions required")
    def test_database_file_is_private(self):
        import os
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "usage.json"
            tracker = TokenUsageTracker(path)
            tracker.close()
            self.assertEqual(os.stat(str(path) + ".sqlite3").st_mode & 0o777, 0o600)

    def test_rejects_database_symlink(self):
        import os
        if not hasattr(os, "symlink"):
            self.skipTest("symlinks are unavailable")
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "usage.json"
            target = Path(d) / "outside.sqlite3"
            target.touch()
            link = Path(str(path) + ".sqlite3")
            try:
                link.symlink_to(target)
            except OSError:
                self.skipTest("symlink creation is not permitted")
            with self.assertRaisesRegex(RuntimeError, "must not be a symlink"):
                TokenUsageTracker(path)

    def test_persists_and_loads(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "usage.json"
            first = TokenUsageTracker(path, session_id="persist")
            first.record(provider="ollama", input_tokens=2, output_tokens=3)
            first.close()
            loaded = TokenUsageTracker(path, session_id="persist")
            self.assertEqual(loaded.summary()["total_tokens"], 5)
            loaded.close()

    def test_migrates_legacy_json_once_without_overwriting_it(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "usage.json"
            legacy = [{
                "session_id": "legacy", "provider": "ollama", "model": "local",
                "input_tokens": 7, "output_tokens": 4, "estimated": False,
                "created_at": "2026-11-09T00:00:00+00:00",
            }]
            original = json.dumps(legacy)
            path.write_text(original, encoding="utf-8")
            tracker = TokenUsageTracker(path, session_id="legacy")
            self.assertEqual(tracker.summary()["total_tokens"], 11)
            tracker.close()
            reloaded = TokenUsageTracker(path, session_id="legacy")
            self.assertEqual(reloaded.summary()["calls"], 1)
            self.assertEqual(path.read_text(encoding="utf-8"), original)
            reloaded.close()


if __name__ == "__main__":
    unittest.main()
