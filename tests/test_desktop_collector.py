import unittest
from unittest.mock import patch

from core.desktop_collector import (
    DesktopCollector,
    UnsupportedDesktopCollector,
    detect_desktop_platform,
)
from core.desktop_metadata_adapter import DesktopMetadataAdapter


class DesktopCollectorContractTests(unittest.TestCase):
    def test_platform_detection_maps_supported_operating_systems(self):
        self.assertEqual(detect_desktop_platform("win32").platform, "windows")
        self.assertEqual(detect_desktop_platform("darwin").platform, "macos")
        self.assertEqual(detect_desktop_platform("linux").platform, "linux")
        self.assertTrue(detect_desktop_platform("linux").supported)

    def test_detection_does_not_claim_native_support_or_permission(self):
        windows = detect_desktop_platform("win32")
        self.assertTrue(windows.native_collector_available)
        self.assertEqual(windows.supported_signals, ("allowlisted_foreground_application_id",))
        for value in ("darwin", "linux"):
            info = detect_desktop_platform(value)
            self.assertFalse(info.native_collector_available)
            self.assertEqual(info.permission_state, "not_requested")
            self.assertEqual(info.supported_signals, ())

    def test_unknown_platform_fails_closed(self):
        info = detect_desktop_platform("freebsd")
        self.assertFalse(info.supported)
        self.assertEqual(info.platform, "unsupported")

    def test_platform_detection_uses_runtime_platform_by_default(self):
        with patch("core.desktop_collector.sys.platform", "darwin"):
            self.assertEqual(detect_desktop_platform().platform, "macos")

    def test_unsupported_collector_cannot_start(self):
        collector = UnsupportedDesktopCollector("linux")
        sink = DesktopMetadataAdapter()
        try:
            self.assertIsInstance(collector, DesktopCollector)
            with self.assertRaises(RuntimeError):
                collector.start(sink, consent_granted=True, indicator=lambda state: None)
            collector.stop()
            collector.stop()
        finally:
            sink.close()

    def test_collector_rejects_invalid_sink(self):
        collector = UnsupportedDesktopCollector("windows")
        with self.assertRaises(TypeError):
            collector.start(object(), consent_granted=True, indicator=lambda state: None)


if __name__ == "__main__":
    unittest.main()
