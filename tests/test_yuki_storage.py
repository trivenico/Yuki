"""Regression tests for atomic local persistence writes."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from yuki_storage import atomic_write_text


class AtomicWriteTextTests(unittest.TestCase):
    def test_replaces_destination_with_complete_utf8_content(self):
        with tempfile.TemporaryDirectory() as folder:
            destination = Path(folder) / "state.json"
            destination.write_text('{"old": true}', encoding="utf-8")

            self.assertTrue(atomic_write_text(destination, '{"message": "こんにちは"}'))
            self.assertEqual(destination.read_text(encoding="utf-8"), '{"message": "こんにちは"}')
            self.assertEqual(list(Path(folder).glob(".state.json.*.tmp")), [])

    def test_replace_failure_preserves_existing_destination_and_cleans_temp(self):
        with tempfile.TemporaryDirectory() as folder:
            destination = Path(folder) / "state.json"
            destination.write_text('{"old": true}', encoding="utf-8")

            with patch("yuki_storage.os.replace", side_effect=OSError("simulated replace failure")):
                self.assertFalse(atomic_write_text(destination, '{"new": true}'))

            self.assertEqual(destination.read_text(encoding="utf-8"), '{"old": true}')
            self.assertEqual(list(Path(folder).glob(".state.json.*.tmp")), [])

    def test_write_failure_returns_false_and_leaves_no_temp_file(self):
        with tempfile.TemporaryDirectory() as folder:
            destination = Path(folder) / "state.json"
            with patch("yuki_storage.os.fsync", side_effect=OSError("simulated fsync failure")):
                self.assertFalse(atomic_write_text(destination, '{"new": true}'))
            self.assertFalse(destination.exists())
            self.assertEqual(list(Path(folder).glob(".state.json.*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
