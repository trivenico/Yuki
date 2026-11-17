import unittest

from defense.security_monitor import DesktopSecurityMonitor, SecurityMonitorError


class FakeProcessMonitor:
    def snapshot(self):
        return {
            "processes": [{"pid": 42, "name": "editor", "status": "running"}],
            "process_count": 1,
            "truncated": False,
            "alerts": [{"type": "configured_process_name_match",
                        "message": "Check process origin."}],
        }


class FakeDetector:
    def observe(self, source_ip, destination_port, *, timestamp=None):
        return {"type": "possible_port_scan", "source_ip": source_ip,
                "unique_ports": 3, "message": "Verify network activity."}


class DesktopSecurityMonitorTests(unittest.TestCase):
    def test_disabled_by_default_and_no_sensor_calls(self):
        monitor = DesktopSecurityMonitor(process_monitor=FakeProcessMonitor())
        self.assertFalse(monitor.enabled)
        with self.assertRaises(SecurityMonitorError):
            monitor.snapshot()

    def test_unified_snapshot_combines_process_and_network_alerts(self):
        monitor = DesktopSecurityMonitor(
            process_monitor=FakeProcessMonitor(),
            port_scan_detector=FakeDetector(),
            enabled=True,
        )
        monitor.observe_connection("192.0.2.8", 443, timestamp=10)
        result = monitor.snapshot()
        self.assertEqual(result["process_count"], 1)
        self.assertEqual(result["processes"][0]["pid"], 42)
        self.assertEqual([a["source"] for a in result["alerts"]], ["process", "network"])
        self.assertTrue(result["read_only"])
        self.assertFalse(result["persisted"])
        self.assertFalse(result["automated_response"])
        self.assertNotIn("source_ip", result["alerts"][-1])

    def test_alert_history_is_bounded(self):
        class Detector:
            def observe(self, source_ip, destination_port, *, timestamp=None):
                return {"type": "event", "message": "Alert"}
        monitor = DesktopSecurityMonitor(port_scan_detector=Detector(),
                                         enabled=True, max_alerts=2)
        for port in (1, 2, 3):
            monitor.observe_connection("192.0.2.1", port)
        self.assertEqual(len(monitor.snapshot()["alerts"]), 2)

    def test_disable_clears_history_and_blocks_collection(self):
        monitor = DesktopSecurityMonitor(port_scan_detector=FakeDetector(), enabled=True)
        monitor.observe_connection("192.0.2.8", 443)
        monitor.disable()
        self.assertFalse(monitor.enabled)
        with self.assertRaises(SecurityMonitorError):
            monitor.snapshot()

    def test_sensor_errors_are_sanitized(self):
        class Broken:
            def snapshot(self):
                raise PermissionError("/private/user/path")
        monitor = DesktopSecurityMonitor(process_monitor=Broken(), enabled=True)
        with self.assertRaisesRegex(SecurityMonitorError, "failed safely") as caught:
            monitor.snapshot()
        self.assertNotIn("/private/user/path", str(caught.exception))

    def test_rejects_invalid_sensor_and_configuration(self):
        with self.assertRaises(ValueError):
            DesktopSecurityMonitor(enabled=1)
        with self.assertRaises(ValueError):
            DesktopSecurityMonitor(max_alerts=0)
        with self.assertRaises(ValueError):
            DesktopSecurityMonitor(process_monitor=object())


if __name__ == "__main__":
    unittest.main()
