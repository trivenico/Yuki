import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from core.token_usage import (
    TokenUsageTracker, create_best_effort_usage_tracker, record_provider_usage,
    provider_call_lock,
)


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

    def test_persists_and_loads(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "usage.json"
            first = TokenUsageTracker(path, session_id="persist")
            first.record(provider="ollama", input_tokens=2, output_tokens=3)
            first.close()
            loaded = TokenUsageTracker(path, session_id="persist")
            self.assertEqual(loaded.summary()["total_tokens"], 5)
            loaded.close()


    def test_best_effort_factory_does_not_raise_when_storage_is_unavailable(self):
        with patch("core.token_usage.TokenUsageTracker", side_effect=OSError("disk unavailable")):
            self.assertIsNone(create_best_effort_usage_tracker("usage", session_id="owner"))



    def test_provider_lock_is_shared_per_adapter_instance(self):
        class Provider:
            pass
        provider = Provider()
        self.assertIs(provider_call_lock(provider), provider_call_lock(provider))
        self.assertIsNot(provider_call_lock(provider), provider_call_lock(Provider()))

    def test_provider_lock_supports_unhashable_adapters(self):
        class Provider:
            __hash__ = None
        provider = Provider()
        self.assertIs(provider_call_lock(provider), provider_call_lock(provider))
        self.assertIsNot(provider_call_lock(provider), provider_call_lock(Provider()))

    def test_provider_lock_supports_slot_only_adapters_without_leaking_them(self):
        class Provider:
            __slots__ = ()
        first, second = Provider(), Provider()
        self.assertIs(provider_call_lock(first), provider_call_lock(second))

    def test_shared_provider_recorder_uses_exact_metadata(self):
        class Provider:
            model = "local-model"
            last_usage = {"input_tokens": 12, "output_tokens": 8, "estimated": False}
        tracker = TokenUsageTracker(session_id="worker")
        self.assertIsNone(record_provider_usage(tracker, Provider(), "prompt", "answer"))
        summary = tracker.summary()
        self.assertEqual(summary["total_tokens"], 20)
        self.assertEqual(summary["exact_events"], 1)
        tracker.close()

    def test_shared_provider_recorder_marks_fallback_as_estimated(self):
        class Provider:
            model = "unknown"
            last_usage = None
        tracker = TokenUsageTracker(session_id="worker-estimate")
        self.assertIsNone(record_provider_usage(tracker, Provider(), "12345678", "1234"))
        summary = tracker.summary()
        self.assertEqual(summary["input_tokens"], 2)
        self.assertEqual(summary["output_tokens"], 1)
        self.assertEqual(summary["estimated_events"], 1)
        tracker.close()

    def test_shared_provider_recorder_returns_storage_error_without_raising(self):
        class Provider:
            last_usage = None
        class BrokenTracker:
            def record(self, **kwargs):
                raise OSError("disk full")
        error = record_provider_usage(BrokenTracker(), Provider(), "prompt", "answer")
        self.assertIsInstance(error, OSError)

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
