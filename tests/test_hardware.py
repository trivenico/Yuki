import unittest

from core.hardware import HardwareInfo, recommend_installed_model


class HardwareDetectionTests(unittest.TestCase):
    def setUp(self):
        self.hardware = HardwareInfo(
            platform="linux",
            cpu_brand="Oracle Linux CPU",
            cpu_count=4,
            ram_total_gb=16.0,
            ram_available_gb=10.0,
            gpu_vendor=None,
            gpu_name=None,
            gpu_vram_gb=None,
            disk_free_gb=20.0,
        )

    def test_recommends_largest_installed_model_with_memory_headroom(self):
        models = [
            {"name": "small:latest", "size": 2 * 1024**3},
            {"name": "medium:latest", "size": 4 * 1024**3},
            {"name": "too-large:latest", "size": 7 * 1024**3},
        ]
        self.assertEqual(
            recommend_installed_model(models, self.hardware),
            "medium:latest",
        )

    def test_uses_stable_alphabetical_choice_when_model_sizes_are_unknown(self):
        models = [{"name": "zeta"}, {"name": "alpha"}]
        self.assertEqual(recommend_installed_model(models, self.hardware), "alpha")

    def test_returns_none_for_empty_or_invalid_inventory(self):
        self.assertIsNone(recommend_installed_model([], self.hardware))
        self.assertIsNone(recommend_installed_model([{"name": ""}], self.hardware))

    def test_unknown_memory_uses_deterministic_fallback(self):
        unknown = HardwareInfo(
            platform="linux",
            cpu_brand="unknown",
            cpu_count=1,
            ram_total_gb=0,
            ram_available_gb=0,
            gpu_vendor=None,
            gpu_name=None,
            gpu_vram_gb=None,
            disk_free_gb=None,
        )
        self.assertEqual(
            recommend_installed_model([{"name": "zeta"}, {"name": "alpha"}], unknown),
            "alpha",
        )


if __name__ == "__main__":
    unittest.main()
