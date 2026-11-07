import tempfile
import unittest
from pathlib import Path

from providers.invocation_trace_store import InvocationTraceStore


class InvocationTraceStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = InvocationTraceStore(str(Path(self.temp.name) / "traces.sqlite3"))
        self.record = {
            "schema_version": 1,
            "invocation_id": "trace-001",
            "provider_id": "ollama",
            "model_id": "local-model:latest",
            "started_at": "2026-11-05T10:00:00+00:00",
            "duration_ms": 12.5,
            "outcome": "success",
            "message_count": 2,
            "input_chars": 24,
            "tool_count": 1,
            "response_chars": 32,
            "tool_call_count": 0,
            "error_type": None,
        }

    def test_append_and_query_metadata_only(self):
        self.store.append(self.record)
        rows = self.store.list_recent(provider_id="ollama", limit=10)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["invocation_id"], "trace-001")
        self.assertNotIn("prompt", rows[0])
        self.assertNotIn("response", rows[0])
        self.assertNotIn("content", rows[0])

    def test_rejects_content_fields_and_invalid_metrics(self):
        unsafe = dict(self.record, prompt="private text")
        with self.assertRaisesRegex(ValueError, "invalid_invocation_record_fields"):
            self.store.append(unsafe)
        invalid = dict(self.record, duration_ms=float("inf"))
        with self.assertRaisesRegex(ValueError, "duration_ms_out_of_range"):
            self.store.append(invalid)
        invalid = dict(self.record, started_at="2026-11-05T10:00:00")
        with self.assertRaisesRegex(ValueError, "timezone_aware"):
            self.store.append(invalid)
        invalid = dict(self.record, outcome=[])
        with self.assertRaisesRegex(ValueError, "invalid_invocation_outcome"):
            self.store.append(invalid)

    def test_records_are_append_only_and_duplicate_ids_fail(self):
        self.store.append(self.record)
        with self.assertRaises(Exception):
            self.store.append(self.record)
        with self.store._connect() as db:
            with self.assertRaisesRegex(Exception, "append_only"):
                db.execute(
                    "UPDATE model_invocations SET outcome='error' WHERE invocation_id=?",
                    ("trace-001",),
                )
            with self.assertRaisesRegex(Exception, "append_only"):
                db.execute(
                    "DELETE FROM model_invocations WHERE invocation_id=?",
                    ("trace-001",),
                )

    def test_query_bounds_and_filters(self):
        with self.assertRaises(ValueError):
            self.store.list_recent(limit=501)
        with self.assertRaisesRegex(ValueError, "invalid_model_id"):
            self.store.list_recent(model_id="bad\nmodel")
        self.store.append(self.record)
        self.assertEqual(self.store.list_recent(model_id="another-model"), [])
        self.assertEqual(len(self.store.list_recent(model_id="local-model:latest")), 1)


if __name__ == "__main__":
    unittest.main()
