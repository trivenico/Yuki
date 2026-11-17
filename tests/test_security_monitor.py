import unittest
from threading import Event, Thread

from defense.security_monitor import DesktopSecurityMonitor, SecurityMonitorError
from defense.ids import PortScanDetector


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
        observed = monitor.observe_connection("192.0.2.8", 443, timestamp=10)
        self.assertEqual(observed["source"], "network")
        self.assertNotIn("source_ip", observed)
        result = monitor.snapshot()
        self.assertEqual(result["process_count"], 1)
        self.assertEqual(result["processes"][0]["pid"], 42)
        self.assertEqual([a["source"] for a in result["alerts"]], ["process", "network"])
        self.assertTrue(result["read_only"])
        self.assertFalse(result["persisted"])
        self.assertFalse(result["automated_response"])
        self.assertNotIn("source_ip", result["alerts"][-1])


    def test_real_detector_integration_emits_only_sanitized_alert(self):
        detector = PortScanDetector(threshold=3, window_seconds=10)
        monitor = DesktopSecurityMonitor(port_scan_detector=detector, enabled=True)
        self.assertIsNone(monitor.observe_connection("192.0.2.44", 80, timestamp=1))
        self.assertIsNone(monitor.observe_connection("192.0.2.44", 443, timestamp=2))
        alert = monitor.observe_connection("192.0.2.44", 8080, timestamp=3)
        self.assertEqual(alert["source"], "network")
        self.assertEqual(alert["type"], "possible_port_scan")
        self.assertNotIn("source_ip", alert)
        self.assertNotIn("unique_ports", alert)
        self.assertNotIn("192.0.2.44", alert["message"])
        snapshot = monitor.snapshot()
        self.assertEqual(snapshot["alerts"][-1], alert)
        self.assertTrue(snapshot["read_only"])
        self.assertFalse(snapshot["automated_response"])

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


    def test_sanitizes_and_bounds_injected_process_data(self):
        class Untrusted:
            def snapshot(self):
                return {
                    "processes": [
                        {"pid": 4, "name": "editor\n" + "x" * 300,
                         "status": "running", "cmdline": "secret"},
                        {"pid": True, "name": "invalid", "status": "running"},
                    ],
                    "process_count": 2,
                    "truncated": False,
                    "alerts": [{"type": "watch\nmatch", "message": "safe\nmessage"}],
                }
        monitor = DesktopSecurityMonitor(process_monitor=Untrusted(), enabled=True)
        result = monitor.snapshot()
        self.assertEqual(len(result["processes"]), 1)
        self.assertLessEqual(len(result["processes"][0]["name"]), 160)
        self.assertNotIn("cmdline", result["processes"][0])
        self.assertNotIn("\n", result["alerts"][0]["message"])


    def test_disable_invalidates_in_flight_sensor_snapshot(self):
        entered = Event()
        release = Event()
        outcomes = []

        class SlowProcessMonitor:
            def snapshot(self):
                entered.set()
                release.wait(2)
                return {"processes": [], "process_count": 0,
                        "truncated": False, "alerts": []}

        monitor = DesktopSecurityMonitor(
            process_monitor=SlowProcessMonitor(), enabled=True,
        )
        worker = Thread(target=lambda: self._capture_snapshot(monitor, outcomes))
        worker.start()
        self.assertTrue(entered.wait(1))
        monitor.disable()
        self.assertFalse(monitor.enabled)
        release.set()
        worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(outcomes, ["disabled"])

    @staticmethod
    def _capture_snapshot(monitor, outcomes):
        try:
            monitor.snapshot()
            outcomes.append("returned")
        except SecurityMonitorError:
            outcomes.append("disabled")

    def test_rejects_malformed_detector_alert(self):
        class BadDetector:
            def observe(self, source_ip, destination_port, *, timestamp=None):
                return ["not", "an", "alert"]
        monitor = DesktopSecurityMonitor(port_scan_detector=BadDetector(), enabled=True)
        with self.assertRaisesRegex(SecurityMonitorError, "invalid alert"):
            monitor.observe_connection("192.0.2.1", 80)

    def test_rejects_invalid_sensor_and_configuration(self):
        with self.assertRaises(ValueError):
            DesktopSecurityMonitor(enabled=1)
        with self.assertRaises(ValueError):
            DesktopSecurityMonitor(max_alerts=0)
        with self.assertRaises(ValueError):
            DesktopSecurityMonitor(process_monitor=object())


if __name__ == "__main__":
    unittest.main()
