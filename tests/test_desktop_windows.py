import unittest

from core.desktop_metadata_adapter import DesktopMetadataAdapter
from core.desktop_windows import WindowsForegroundAppCollector


class WindowsForegroundAppCollectorTests(unittest.TestCase):
    def setUp(self):
        self.sink = DesktopMetadataAdapter(allowed_app_ids=("ide",))
        self.indicator_states = []
        self.collector = WindowsForegroundAppCollector(
            {"code.exe": "ide"},
            reader=lambda: "CODE.EXE",
        )

    def tearDown(self):
        self.collector.stop()
        self.sink.close()

    def test_requires_explicit_consent_and_visible_indicator(self):
        with self.assertRaises(PermissionError):
            self.collector.start(
                self.sink, consent_granted=False,
                indicator=self.indicator_states.append,
            )
        with self.assertRaises(ValueError):
            self.collector.start(self.sink, consent_granted=True, indicator=None)
        self.assertEqual(self.indicator_states, [])

    def test_start_indicator_poll_and_stop(self):
        self.collector.start(
            self.sink, consent_granted=True,
            indicator=self.indicator_states.append,
        )
        self.assertEqual(self.indicator_states, [True])
        self.assertTrue(self.collector.poll_once())
        event = self.sink.read_next()
        self.assertEqual(dict(event.metadata), {"application_id": "ide"})
        self.collector.stop()
        self.assertEqual(self.indicator_states, [True, False])
        self.assertFalse(self.collector.poll_once())

    def test_unmapped_process_is_not_emitted(self):
        collector = WindowsForegroundAppCollector(
            {"code.exe": "ide"}, reader=lambda: "password-manager.exe",
        )
        try:
            collector.start(self.sink, consent_granted=True, indicator=lambda state: None)
            self.assertFalse(collector.poll_once())
            self.assertIsNone(self.sink.read_next())
        finally:
            collector.stop()

    def test_reader_output_is_reduced_to_executable_basename(self):
        collector = WindowsForegroundAppCollector(
            {"code.exe": "ide"},
            reader=lambda: r"C:\Users\owner\Apps\Code.exe",
        )
        try:
            collector.start(self.sink, consent_granted=True, indicator=lambda state: None)
            self.assertTrue(collector.poll_once())
            self.assertEqual(
                dict(self.sink.read_next().metadata), {"application_id": "ide"}
            )
        finally:
            collector.stop()

    def test_invalid_mapping_is_rejected(self):
        with self.assertRaises(ValueError):
            WindowsForegroundAppCollector({"not-an-executable": "ide"})

    def test_indicator_failure_prevents_activation(self):
        def fail(_state):
            raise RuntimeError("indicator unavailable")
        with self.assertRaises(RuntimeError):
            self.collector.start(self.sink, consent_granted=True, indicator=fail)
        self.assertFalse(self.collector.poll_once())


if __name__ == "__main__":
    unittest.main()
