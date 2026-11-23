import json
import tempfile
import unittest
from pathlib import Path

from core.diagnostics import DiagnosticEventStore, record_diagnostic_event


class DiagnosticEventStoreTests(unittest.TestCase):
    def test_persists_only_safe_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "diagnostics.json"
            store = DiagnosticEventStore(path)
            store.record("cli", "startup_failed", RuntimeError("secret token and private prompt"))
            raw = path.read_text(encoding="utf-8")
            self.assertNotIn("secret token", raw)
            self.assertNotIn("private prompt", raw)
            item = store.recent()[0]
            self.assertEqual(item["component"], "cli")
            self.assertEqual(item["event"], "startup_failed")
            self.assertEqual(item["error_type"], "RuntimeError")

    def test_reading_missing_history_does_not_create_storage(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "missing" / "diagnostics.json"
            self.assertEqual(DiagnosticEventStore(path).recent(), [])
            self.assertFalse(path.parent.exists())

    def test_history_is_bounded_to_latest_one_hundred(self):
        with tempfile.TemporaryDirectory() as directory:
            store = DiagnosticEventStore(Path(directory) / "diagnostics.json")
            for index in range(105):
                store.record("cli", f"event_{index}")
            events = store.recent(100)
            self.assertEqual(len(events), 100)
            self.assertEqual(events[0]["event"], "event_5")
            self.assertEqual(events[-1]["event"], "event_104")

    def test_rejects_invalid_identifiers_and_corrupt_history(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "diagnostics.json"
            store = DiagnosticEventStore(path)
            with self.assertRaises(ValueError):
                store.record("../private", "startup")
            path.write_text(json.dumps([{"secret": "value"}]), encoding="utf-8")
            with self.assertRaises(RuntimeError):
                store.recent()

    def test_rejects_symlinked_history(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "outside.json"
            target.write_text("[]", encoding="utf-8")
            link = root / "diagnostics.json"
            try:
                link.symlink_to(target)
            except (OSError, NotImplementedError):
                self.skipTest("symlinks are unavailable on this platform")
            with self.assertRaises(RuntimeError):
                DiagnosticEventStore(link).recent()

    def test_best_effort_recorder_does_not_raise(self):
        self.assertIsInstance(record_diagnostic_event("bad component", "startup"), bool)


if __name__ == "__main__":
    unittest.main()
