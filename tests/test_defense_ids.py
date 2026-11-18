import unittest
from concurrent.futures import ThreadPoolExecutor

from defense.ids import IDSError, PacketSniffer, PortScanDetector


class PortScanDetectorTests(unittest.TestCase):
    def test_detects_distinct_ports_once_per_window(self):
        detector = PortScanDetector(threshold=3, window_seconds=10)
        self.assertIsNone(detector.observe("192.0.2.10", 22, timestamp=1))
        self.assertIsNone(detector.observe("192.0.2.10", 23, timestamp=2))
        alert = detector.observe("192.0.2.10", 24, timestamp=3)
        self.assertEqual(alert["type"], "possible_port_scan")
        self.assertEqual(alert["unique_ports"], 3)
        self.assertIsNone(detector.observe("192.0.2.10", 25, timestamp=4))

    def test_allows_new_alert_after_suppression_window(self):
        detector = PortScanDetector(threshold=3, window_seconds=5)
        for port in (20, 21, 22):
            alert = detector.observe("192.0.2.1", port, timestamp=1)
        self.assertIsNotNone(alert)
        for port in (30, 31, 32):
            alert = detector.observe("192.0.2.1", port, timestamp=10)
        self.assertIsNotNone(alert)

    def test_expires_old_events(self):
        detector = PortScanDetector(threshold=3, window_seconds=5)
        detector.observe("192.0.2.1", 20, timestamp=1)
        detector.observe("192.0.2.1", 21, timestamp=2)
        self.assertIsNone(detector.observe("192.0.2.1", 22, timestamp=10))

    def test_bounds_source_cardinality_with_lru_eviction(self):
        detector = PortScanDetector(threshold=3, max_sources=2)
        detector.observe("192.0.2.1", 20, timestamp=1)
        detector.observe("192.0.2.2", 20, timestamp=1)
        detector.observe("192.0.2.3", 20, timestamp=1)
        self.assertEqual(detector.tracked_source_count, 2)
        self.assertNotIn("192.0.2.1", detector._events)

    def test_bounds_events_per_source(self):
        detector = PortScanDetector(threshold=3, max_events_per_source=3)
        for port in range(100, 200):
            detector.observe("192.0.2.1", port, timestamp=1)
        self.assertLessEqual(len(detector._events["192.0.2.1"]), 3)

    def test_rejects_invalid_inputs_and_non_finite_timestamp(self):
        detector = PortScanDetector()
        for address in (None, "not-an-ip"):
            with self.assertRaises(ValueError):
                detector.observe(address, 80, timestamp=1)
        for timestamp in (float("nan"), float("inf"), True, "1"):
            with self.assertRaises(ValueError):
                detector.observe("192.0.2.1", 80, timestamp=timestamp)
        with self.assertRaises(ValueError):
            detector.observe("192.0.2.1", True, timestamp=1)

    def test_rejects_out_of_order_timestamps(self):
        detector = PortScanDetector(threshold=3)
        detector.observe("192.0.2.1", 80, timestamp=5)
        with self.assertRaisesRegex(ValueError, "must not move backwards"):
            detector.observe("192.0.2.1", 81, timestamp=4)

    def test_event_at_window_boundary_is_expired(self):
        detector = PortScanDetector(threshold=3, window_seconds=5)
        detector.observe("192.0.2.1", 80, timestamp=1)
        detector.observe("192.0.2.1", 81, timestamp=2)
        self.assertIsNone(detector.observe("192.0.2.1", 82, timestamp=6))


    def test_concurrent_observations_keep_state_consistent(self):
        detector = PortScanDetector(threshold=3, max_sources=4,
                                    max_events_per_source=64)
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(
                lambda port: detector.observe("192.0.2.44", port),
                range(100, 140),
            ))
        self.assertEqual(detector.tracked_source_count, 1)
        self.assertEqual(sum(result is not None for result in results), 1)
        self.assertLessEqual(len(detector._events["192.0.2.44"]), 64)

    def test_packet_capture_is_disabled_by_default(self):
        with self.assertRaises(IDSError):
            PacketSniffer().sniff()


if __name__ == "__main__":
    unittest.main()
