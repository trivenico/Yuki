import io
import json
import logging
import unittest

from core.observability import JsonLogFormatter, configure_logging, log_event


class ObservabilityTests(unittest.TestCase):
    def test_formatter_serializes_allowlisted_metadata_only(self):
        record = logging.LogRecord(
            "yuki.task", logging.INFO, __file__, 10, "private prompt must not appear", (), None
        )
        record.event = "task.finished"
        record.component = "task_runner"
        record.task_id = "abc123"
        record.status = "completed"
        record.duration_ms = 12
        record.prompt = "do not log this"
        rendered = JsonLogFormatter().format(record)
        payload = json.loads(rendered)
        self.assertEqual(payload["event"], "task.finished")
        self.assertEqual(payload["duration_ms"], 12)
        self.assertNotIn("private prompt", rendered)
        self.assertNotIn("do not log this", rendered)
        self.assertNotIn("prompt", payload)

    def test_formatter_ignores_invalid_metadata_types(self):
        record = logging.LogRecord("yuki", logging.INFO, __file__, 10, "event", (), None)
        record.duration_ms = True
        record.task_id = {"secret": "value"}
        rendered = JsonLogFormatter().format(record)
        payload = json.loads(rendered)
        self.assertNotIn("duration_ms", payload)
        self.assertNotIn("task_id", payload)

    def test_configure_logging_and_event_helper(self):
        stream = io.StringIO()
        configure_logging(level="INFO", stream=stream)
        logger = logging.getLogger("yuki.observability.test")
        log_event(
            logger,
            logging.INFO,
            "task.finished",
            component="task_runner",
            task_id="abc",
            status="completed",
            duration_ms=20,
            step_count=2,
        )
        payload = json.loads(stream.getvalue())
        self.assertEqual(payload["event"], "task.finished")
        self.assertEqual(payload["task_id"], "abc")
        self.assertEqual(payload["step_count"], 2)

    def test_invalid_log_level_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "invalid log level"):
            configure_logging(level="TRACE")


if __name__ == "__main__":
    unittest.main()
