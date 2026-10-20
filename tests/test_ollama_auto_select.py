import json
import unittest
from io import BytesIO
from unittest.mock import patch

from core.hardware import HardwareInfo
from providers.ollama import OllamaProvider


class OllamaHardwareSelectionTests(unittest.TestCase):
    def setUp(self):
        self.hardware = HardwareInfo(
            os_name="linux",
            os_version="test",
            architecture="x86_64",
            cpu_brand="test CPU",
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

    @patch("core.hardware.detect_hardware")
    @patch("providers.ollama.open_provider_request")
    def test_selects_largest_installed_model_that_fits_ram_budget(self, open_request, detect):
        detect.return_value = self.hardware
        open_request.return_value = BytesIO(
            json.dumps(
                {
                    "models": [
                        {"name": "small:latest", "size": 2 * 1024**3},
                        {"name": "medium:latest", "size": 4 * 1024**3},
                        {"name": "too-large:latest", "size": 7 * 1024**3},
                    ]
                }
            ).encode()
        )
        provider = OllamaProvider(model="", auto_select=True)
        self.assertEqual(provider.select_model_for_hardware(), "medium:latest")
        self.assertEqual(provider.model, "medium:latest")

    @patch("core.hardware.detect_hardware")
    @patch("providers.ollama.open_provider_request")
    def test_refuses_auto_selection_when_no_installed_model_fits(self, open_request, detect):
        detect.return_value = self.hardware
        open_request.return_value = BytesIO(
            json.dumps({"models": [{"name": "large:latest", "size": 40 * 1024**3}]}).encode()
        )
        provider = OllamaProvider(model="", auto_select=True)
        with self.assertRaisesRegex(RuntimeError, "No installed Ollama model"):
            provider.select_model_for_hardware()

    @patch("providers.ollama.open_provider_request")
    def test_model_inventory_rejects_malformed_response(self, open_request):
        open_request.return_value = BytesIO(b'{"models":"not-a-list"}')
        provider = OllamaProvider(model="", auto_select=True)
        with self.assertRaisesRegex(RuntimeError, "invalid model inventory"):
            provider.list_models()


if __name__ == "__main__":
    unittest.main()
