"""Privacy guarantees for structured operational logging."""
import io
import json
import logging
import unittest

from core.observability import JsonLogFormatter, log_event


class ObservabilityTests(unittest.TestCase):
    def test_formatter_emits_only_allowlisted_metadata(self):
        record = logging.LogRecord(
            "yuki.test", logging.INFO, __file__, 1, "sensitive prompt must not be serialized", (), None
        )
        record.event = "approval.requested"
        record.component = "approval_flow"
        record.status = "requested"
        record.reason_code = "notes.write"
        record.password = "secret-value"
        encoded = JsonLogFormatter().format(record)
        parsed = json.loads(encoded)
        self.assertEqual(parsed["event"], "approval.requested")
        self.assertNotIn("msg", parsed)
        self.assertNotIn("password", parsed)
        self.assertNotIn("sensitive prompt", encoded)
        self.assertNotIn("secret-value", encoded)

    def test_log_event_bounds_and_sanitizes_field_types(self):
        stream = io.StringIO()
        logger = logging.getLogger("yuki.test.observability")
        logger.handlers.clear()
        logger.propagate = False
        handler = logging.StreamHandler(stream)
        handler.setFormatter(JsonLogFormatter())
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        try:
            log_event(
                logger, logging.INFO, "test.event", component="test",
                task_id="x" * 200, status="ok", reason_code="safe",
                duration_ms=-1, step_count=True,
            )
        finally:
            logger.removeHandler(handler)
            handler.close()
        event = json.loads(stream.getvalue())
        self.assertEqual(len(event["task_id"]), 80)
        self.assertEqual(event["status"], "ok")
        self.assertNotIn("duration_ms", event)
        self.assertNotIn("step_count", event)


if __name__ == "__main__":
    unittest.main()
