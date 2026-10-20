import unittest
from unittest.mock import Mock, patch

from core.hardware import (
    HardwareInfo,
    _macos_memory,
    _parse_linux_memory,
    detect_hardware,
    recommend_installed_model,
)


class HardwareDetectionTests(unittest.TestCase):
    def setUp(self):
        self.hardware = HardwareInfo(
            os_name="linux",
            os_version="test",
            architecture="x86_64",
            cpu_brand="Oracle Linux CPU",
            cpu_count=4,
            ram_total_gb=16.0,
            ram_available_gb=10.0,
            gpu_vendor=None,
            gpu_name=None,
            gpu_vram_gb=None,
            gpu_detection_method=None,
            disk_free_gb=20.0,
            inference_runtimes=("ollama",),
            warnings=(),
        )

    def test_linux_memory_parser_uses_available_memory(self):
        total, available = _parse_linux_memory(
            "MemTotal: 16777216 kB\nMemAvailable: 10485760 kB\n"
        )
        self.assertEqual(total, 16.0)
        self.assertEqual(available, 10.0)

    def test_linux_memory_parser_falls_back_to_total_when_available_missing(self):
        self.assertEqual(_parse_linux_memory("MemTotal: 1048576 kB\n"), (1.0, 1.0))

    @patch("core.hardware.subprocess.run")
    def test_macos_memory_uses_sysctl_and_vm_stat(self, run):
        run.side_effect = [
            Mock(stdout=str(16 * 1024**3)),
            Mock(
                stdout=(
                    "Mach Virtual Memory Statistics: (page size of 4096 bytes)\n"
                    "Pages free: 1024.\n"
                    "Pages inactive: 2048.\n"
                )
            ),
        ]
        self.assertEqual(_macos_memory(), (16.0, 0.01))
        self.assertEqual(run.call_count, 2)
        self.assertTrue(all(call.kwargs["timeout"] == 2 for call in run.call_args_list))

    @patch("core.hardware.shutil.which", return_value=None)
    @patch("core.hardware._nvidia_gpu", return_value=None)
    @patch("core.hardware._cpu_brand", return_value="Test CPU")
    @patch("core.hardware._memory_info", return_value=(8.0, 4.0))
    @patch("core.hardware.shutil.disk_usage")
    @patch("core.hardware.platform.system", return_value="Linux")
    @patch("core.hardware.platform.machine", return_value="x86_64")
    @patch("core.hardware.platform.version", return_value="test-version")
    @patch("core.hardware.os.cpu_count", return_value=4)
    def test_detection_degrades_gracefully_without_optional_runtimes(
        self, _cpu_count, _version, _machine, _system, disk_usage, _memory, _cpu, _gpu, _which
    ):
        disk_usage.return_value = Mock(free=10 * 1024**3)
        result = detect_hardware()
        self.assertEqual(result.os_name, "linux")
        self.assertEqual(result.architecture, "x86_64")
        self.assertEqual(result.cpu_count, 4)
        self.assertEqual(result.ram_available_gb, 4.0)
        self.assertIsNone(result.gpu_vendor)
        self.assertEqual(result.inference_runtimes, ())
        self.assertTrue(any("No supported local inference executable" in item for item in result.warnings))
        self.assertEqual(result.to_dict()["inference_runtimes"], [])

    @patch("core.hardware.platform.machine", return_value="arm64")
    @patch("core.hardware.platform.system", return_value="Darwin")
    @patch("core.hardware._memory_info", return_value=(16.0, 8.0))
    @patch("core.hardware._cpu_brand", return_value="Apple Silicon")
    @patch("core.hardware._nvidia_gpu", return_value=None)
    @patch("core.hardware.shutil.which", return_value=None)
    @patch("core.hardware.shutil.disk_usage")
    def test_apple_silicon_is_reported_without_claiming_metal_support(
        self, disk_usage, _which, _gpu, _cpu, _memory, _system, _machine
    ):
        disk_usage.return_value = Mock(free=10 * 1024**3)
        result = detect_hardware()
        self.assertEqual(result.gpu_vendor, "apple")
        self.assertIn("runtime acceleration unverified", result.gpu_name)
        self.assertIn("platform heuristic", result.gpu_detection_method)

    def test_recommends_largest_installed_model_with_memory_headroom(self):
        models = [
            {"name": "small:latest", "size": 2 * 1024**3},
            {"name": "medium:latest", "size": 4 * 1024**3},
            {"name": "too-large:latest", "size": 7 * 1024**3},
        ]
        self.assertEqual(recommend_installed_model(models, self.hardware), "medium:latest")

    def test_unknown_model_sizes_are_not_claimed_as_safe_fits(self):
        self.assertIsNone(
            recommend_installed_model([{"name": "zeta"}, {"name": "alpha"}], self.hardware)
        )

    def test_returns_no_recommendation_when_every_model_exceeds_memory_budget(self):
        models = [{"name": "large:latest", "size": 40 * 1024**3}]
        self.assertIsNone(recommend_installed_model(models, self.hardware))

    def test_returns_none_for_empty_or_invalid_inventory(self):
        self.assertIsNone(recommend_installed_model([], self.hardware))
        self.assertIsNone(recommend_installed_model([{"name": ""}], self.hardware))

    def test_unknown_memory_fails_closed_without_a_recommendation(self):
        unknown = HardwareInfo(
            os_name="linux",
            os_version="unknown",
            architecture="unknown",
            cpu_brand="unknown",
            cpu_count=1,
            ram_total_gb=0.0,
            ram_available_gb=0.0,
            gpu_vendor=None,
            gpu_name=None,
            gpu_vram_gb=None,
            gpu_detection_method=None,
            disk_free_gb=None,
            inference_runtimes=(),
            warnings=(),
        )
        self.assertIsNone(
            recommend_installed_model([{"name": "zeta"}, {"name": "alpha"}], unknown)
        )


if __name__ == "__main__":
    unittest.main()
