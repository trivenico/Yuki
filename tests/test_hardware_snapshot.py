import unittest
from unittest.mock import patch

from providers.hardware_snapshot import detect_hardware, _posix_memory


class HardwareSnapshotTests(unittest.TestCase):
    def test_snapshot_reports_platform_and_never_claims_gpu_detection(self):
        with patch("providers.hardware_snapshot.platform.system", return_value="Linux"), \
             patch("providers.hardware_snapshot.platform.machine", return_value="x86_64"), \
             patch("providers.hardware_snapshot.os.cpu_count", return_value=8), \
             patch("providers.hardware_snapshot.os.name", "posix"), \
             patch("providers.hardware_snapshot._posix_memory", return_value=(16_000, 8_000)):
            result = detect_hardware()
        self.assertEqual(result["operating_system"], "Linux")
        self.assertEqual(result["architecture"], "x86_64")
        self.assertEqual(result["logical_cpu_count"], 8)
        self.assertEqual(result["total_memory_bytes"], 16_000)
        self.assertEqual(result["available_memory_bytes"], 8_000)
        self.assertIsNone(result["gpu"]["detected"])
        self.assertEqual(result["gpu"]["status"], "not_probed")
        self.assertFalse(result["network_access"])
        self.assertFalse(result["automatic_model_download"])

    def test_invalid_cpu_count_is_unknown(self):
        with patch("providers.hardware_snapshot.platform.system", return_value="Linux"), \
             patch("providers.hardware_snapshot.platform.machine", return_value="unknown"), \
             patch("providers.hardware_snapshot.os.cpu_count", return_value=0), \
             patch("providers.hardware_snapshot.os.name", "posix"), \
             patch("providers.hardware_snapshot._posix_memory", return_value=(None, None)):
            result = detect_hardware()
        self.assertIsNone(result["logical_cpu_count"])
        self.assertIsNone(result["total_memory_bytes"])
        self.assertEqual(result["memory_source"], "unavailable")

    def test_posix_memory_bounds_available_by_total(self):
        values = {"SC_PAGE_SIZE": 4096, "SC_PHYS_PAGES": 100, "SC_AVPHYS_PAGES": 120}
        # sysconf is absent on Windows; create=True keeps this POSIX-path unit
        # test portable while still exercising the production helper.
        with patch("providers.hardware_snapshot.os.sysconf", side_effect=lambda key: values[key], create=True):
            total, available = _posix_memory()
        self.assertEqual(total, 409600)
        self.assertEqual(available, total)

    def test_unsupported_sysconf_key_returns_unknown(self):
        with patch("providers.hardware_snapshot.os.sysconf", side_effect=ValueError("unsupported"), create=True):
            total, available = _posix_memory()
        self.assertIsNone(total)
        self.assertIsNone(available)


if __name__ == "__main__":
    unittest.main()
