import unittest
from unittest.mock import patch

from core.device_diagnostics import discover_devices, format_device_report, _linux_usb_devices


class DeviceDiagnosticsTests(unittest.TestCase):
    def test_report_is_read_only_and_does_not_claim_health_checks(self):
        report = format_device_report({
            "platform": "TestOS",
            "devices": [{"kind": "volume", "mountpoint": "/mnt/test",
                         "filesystem": "ext4", "total_bytes": 1024 ** 3,
                         "free_bytes": 512 * 1024 ** 2, "used_percent": 50.0,
                         "read_only": True}],
        })
        self.assertIn("read-only", report)
        self.assertIn("No changes made", report)
        self.assertIn("filesystem integrity", report)
        self.assertIn("50.0% used", report)

    def test_report_handles_empty_inventory(self):
        report = format_device_report({"platform": "test", "devices": []})
        self.assertIn("No supported USB identity", report)

    def test_linux_usb_inventory_ignores_nodes_without_identity(self):
        with patch("core.device_diagnostics.platform.system", return_value="Linux"), \
             patch("core.device_diagnostics.os.path.isdir", return_value=False):
            self.assertEqual(_linux_usb_devices("/not-a-real-sysfs"), [])

    def test_discovery_contract_is_read_only(self):
        with patch("core.device_diagnostics._linux_usb_devices", return_value=[]), \
             patch("core.device_diagnostics._mounted_volumes", return_value=[]), \
             patch("core.device_diagnostics.platform.system", return_value="Linux"):
            result = discover_devices()
        self.assertTrue(result["ok"])
        self.assertTrue(result["read_only"])
        self.assertIn("serial numbers", result["privacy"])
        self.assertEqual(result["devices"], [])


if __name__ == "__main__":
    unittest.main()
