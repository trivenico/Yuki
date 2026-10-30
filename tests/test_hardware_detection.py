import unittest
from unittest.mock import patch, Mock

from core.hardware import detect_hardware, _nvidia_gpus
from core.model_recommendations import recommend_model_profiles


class HardwareDetectionTests(unittest.TestCase):
    @patch("core.hardware.shutil.disk_usage")
    @patch("core.hardware._torch_accelerators", return_value=[])
    @patch("core.hardware._nvidia_gpus", return_value=[])
    @patch("core.hardware._memory_bytes", return_value=(16 * 1024**3, 8 * 1024**3))
    def test_detection_is_local_and_returns_sanitized_inventory(self, memory, nvidia, torch, disk):
        disk.return_value = Mock(total=100 * 1024**3, free=40 * 1024**3)
        report = detect_hardware(model_cache_path="/tmp")
        self.assertEqual(report["ram_total_bytes"], 16 * 1024**3)
        self.assertEqual(report["disk_free_bytes"], 40 * 1024**3)
        self.assertEqual(report["accelerators"], [])
        self.assertFalse(report["network_scanned"])
        self.assertFalse(report["model_loaded"])

    @patch("core.hardware.shutil.which", return_value="/usr/bin/nvidia-smi")
    @patch("core.hardware.subprocess.run")
    def test_nvidia_smi_output_is_parsed_without_shell(self, run, which):
        run.return_value = Mock(returncode=0, stdout="GPU A, 8192, 550.1\n", stderr="")
        devices = _nvidia_gpus()
        self.assertEqual(devices[0]["memory_total_bytes"], 8192 * 1024**2)
        self.assertEqual(devices[0]["backend"], "cuda")
        self.assertEqual(run.call_args.args[0][0], "/usr/bin/nvidia-smi")
        self.assertNotIn("shell", run.call_args.kwargs)

    def test_gpu_recommendations_are_conservative_and_do_not_auto_activate(self):
        report = {"schema_version": 1, "ram_total_bytes": 32 * 1024**3,
                  "disk_free_bytes": 100 * 1024**3,
                  "accelerators": [{"backend": "cuda", "memory_total_bytes": 12 * 1024**3}]}
        result = recommend_model_profiles(report, task="coding")
        self.assertTrue(result)
        self.assertEqual(result[-1]["resource_basis"], "gpu_vram")
        self.assertFalse(result[-1]["automatic_activation"])
        self.assertEqual(result[-1]["task"], "coding")

    def test_cpu_fallback_uses_ram_and_warns_about_speed(self):
        report = {"schema_version": 1, "ram_total_bytes": 16 * 1024**3,
                  "disk_free_bytes": 30 * 1024**3, "accelerators": []}
        result = recommend_model_profiles(report)
        self.assertEqual(result[-1]["resource_basis"], "system_ram")
        self.assertIn("slower", result[-1]["caution"])

    def test_low_storage_surfaces_download_warning(self):
        report = {"schema_version": 1, "ram_total_bytes": 8 * 1024**3,
                  "disk_free_bytes": 2 * 1024**3, "accelerators": []}
        result = recommend_model_profiles(report)
        self.assertEqual(result[0]["profile"], "storage_limited")
        self.assertFalse(result[0]["automatic_download"])

    def test_invalid_inputs_are_rejected(self):
        with self.assertRaises(ValueError):
            recommend_model_profiles({}, task="unknown")
        with self.assertRaises(ValueError):
            recommend_model_profiles({"schema_version": 2}, max_results=0)


if __name__ == "__main__":
    unittest.main()
