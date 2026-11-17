import unittest

from defense.process_monitor import ProcessMonitorError, ReadOnlyProcessMonitor


class ReadOnlyProcessMonitorTests(unittest.TestCase):
    def test_returns_minimal_process_metadata(self):
        monitor = ReadOnlyProcessMonitor(provider=lambda: [
            {"pid": 10, "name": "editor", "status": "running",
             "cmdline": ["editor", "--token=secret"], "username": "private",
             "exe": "/private/path/editor", "environ": {"TOKEN": "secret"}},
        ])
        result = monitor.snapshot()
        self.assertEqual(result["processes"], [
            {"pid": 10, "name": "editor", "status": "running"}
        ])
        self.assertNotIn("cmdline", result["processes"][0])
        self.assertNotIn("username", result["processes"][0])
        self.assertFalse(result["persisted"])
        self.assertTrue(result["read_only"])

    def test_snapshot_is_bounded_and_reports_truncation(self):
        monitor = ReadOnlyProcessMonitor(
            max_processes=2,
            provider=lambda: [
                {"pid": 1, "name": "one", "status": "running"},
                {"pid": 2, "name": "two", "status": "sleeping"},
                {"pid": 3, "name": "three", "status": "running"},
            ],
        )
        result = monitor.snapshot()
        self.assertEqual(result["process_count"], 2)
        self.assertTrue(result["truncated"])

    def test_exact_user_configured_watchlist_emits_alert_only(self):
        monitor = ReadOnlyProcessMonitor(
            suspicious_names=["unknown-tool"],
            provider=lambda: [{"pid": 7, "name": "Unknown-Tool", "status": "running"}],
        )
        alert = monitor.snapshot()["alerts"][0]
        self.assertEqual(alert["type"], "configured_process_name_match")
        self.assertEqual(alert["pid"], 7)
        self.assertIn("verify", alert["message"])

    def test_invalid_and_duplicate_pids_are_ignored(self):
        monitor = ReadOnlyProcessMonitor(provider=lambda: [
            {"pid": True, "name": "bad", "status": "running"},
            {"pid": 1, "name": "first", "status": "running"},
            {"pid": 1, "name": "duplicate", "status": "running"},
            {"pid": -1, "name": "bad", "status": "running"},
        ])
        result = monitor.snapshot()
        self.assertEqual(result["process_count"], 1)
        self.assertEqual(result["processes"][0]["name"], "first")

    def test_process_names_are_sanitized_and_bounded(self):
        monitor = ReadOnlyProcessMonitor(provider=lambda: [
            {"pid": 1, "name": "bad\nname" + "x" * 200, "status": None},
        ])
        item = monitor.snapshot()["processes"][0]
        self.assertNotIn("\n", item["name"])
        self.assertLessEqual(len(item["name"]), 160)
        self.assertEqual(item["status"], "unknown")

    def test_provider_errors_fail_closed(self):
        def broken():
            raise PermissionError("private path")
        with self.assertRaises(ProcessMonitorError):
            ReadOnlyProcessMonitor(provider=broken).snapshot()


    def test_skips_process_that_exits_during_metadata_read(self):
        class VanishedProcess:
            @property
            def info(self):
                raise ProcessLookupError("private process detail")

        monitor = ReadOnlyProcessMonitor(provider=lambda: [
            VanishedProcess(),
            {"pid": 2, "name": "stable", "status": "running"},
        ])
        result = monitor.snapshot()
        self.assertEqual(result["process_count"], 1)
        self.assertEqual(result["processes"][0]["pid"], 2)

    def test_watchlist_normalizes_control_characters(self):
        monitor = ReadOnlyProcessMonitor(
            suspicious_names=["bad\\n-tool"],
            provider=lambda: [{"pid": 9, "name": "bad-tool", "status": "running"}],
        )
        self.assertEqual(len(monitor.snapshot()["alerts"]), 1)


    def test_watchlist_size_is_bounded(self):
        def unbounded_names():
            index = 0
            while True:
                yield f"process-{index}"
                index += 1

        with self.assertRaisesRegex(ValueError, "more than 1000"):
            ReadOnlyProcessMonitor(suspicious_names=unbounded_names())

    def test_configuration_validation(self):
        for value in (0, 5001, True):
            with self.assertRaises(ValueError):
                ReadOnlyProcessMonitor(max_processes=value)
        with self.assertRaises(ValueError):
            ReadOnlyProcessMonitor(suspicious_names="not-an-iterable-of-names")


if __name__ == "__main__":
    unittest.main()
