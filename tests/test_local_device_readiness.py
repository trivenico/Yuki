import tempfile
import unittest
from pathlib import Path

from providers.local_device_readiness import (
    LocalDeviceReadinessError,
    _model_compatibility,
    build_local_device_report,
)


class LocalDeviceReadinessTests(unittest.TestCase):
    def model(self, **overrides):
        value = {
            "model_id": "compact-test-model",
            "minimum_ram_bytes": 512 * 1024**2,
            "recommended_ram_bytes": 1024 * 1024**2,
            "download_bytes": 128 * 1024**2,
            "minimum_free_disk_bytes": 256 * 1024**2,
            "supported_operating_systems": ["Linux", "Windows", "Darwin"],
            "supported_architectures": ["x86_64", "AMD64", "aarch64", "arm64"],
            "requires_gpu": False,
        }
        value.update(overrides)
        return value

    def test_compatible_model_is_recommended_when_budget_allows(self):
        result = _model_compatibility(
            self.model(), os_name="Linux", architecture="x86_64",
            runtime_budget=2 * 1024**3, free_disk=4 * 1024**3,
        )
        self.assertEqual(result["status"], "recommended")
        self.assertTrue(result["compatible"])

    def test_minimum_fit_is_not_misreported_as_recommended(self):
        result = _model_compatibility(
            self.model(), os_name="Linux", architecture="x86_64",
            runtime_budget=600 * 1024**2, free_disk=4 * 1024**3,
        )
        self.assertEqual(result["status"], "fits_minimum")
        self.assertFalse(result["recommended"])

    def test_insufficient_disk_and_ram_are_explained(self):
        result = _model_compatibility(
            self.model(), os_name="Linux", architecture="x86_64",
            runtime_budget=128 * 1024**2, free_disk=64 * 1024**2,
        )
        self.assertFalse(result["compatible"])
        self.assertIn("insufficient_free_disk", result["reasons"])
        self.assertIn("insufficient_advisory_ram_budget", result["reasons"])

    def test_unverified_gpu_requirement_is_not_admitted(self):
        result = _model_compatibility(
            self.model(requires_gpu=True), os_name="Linux", architecture="x86_64",
            runtime_budget=2 * 1024**3, free_disk=4 * 1024**3,
        )
        self.assertFalse(result["compatible"])
        self.assertIn("gpu_requirement_not_verified", result["reasons"])

    def test_unsupported_platform_is_rejected(self):
        result = _model_compatibility(
            self.model(supported_operating_systems=["Linux"]),
            os_name="Windows", architecture="x86_64",
            runtime_budget=2 * 1024**3, free_disk=4 * 1024**3,
        )
        self.assertIn("unsupported_operating_system", result["reasons"])

    def test_unknown_fields_are_rejected(self):
        with self.assertRaisesRegex(LocalDeviceReadinessError, "unsupported"):
            _model_compatibility(
                self.model(shell_command="install arbitrary thing"),
                os_name="Linux", architecture="x86_64",
                runtime_budget=2 * 1024**3, free_disk=4 * 1024**3,
            )

    def test_recommended_memory_must_not_be_below_minimum(self):
        with self.assertRaisesRegex(LocalDeviceReadinessError, "recommended_ram"):
            _model_compatibility(
                self.model(recommended_ram_bytes=1),
                os_name="Linux", architecture="x86_64",
                runtime_budget=2 * 1024**3, free_disk=4 * 1024**3,
            )

    def test_report_requires_existing_model_directory(self):
        with tempfile.TemporaryDirectory() as temp:
            missing = Path(temp) / "missing"
            with self.assertRaisesRegex(LocalDeviceReadinessError, "must_exist"):
                build_local_device_report(missing)


if __name__ == "__main__":
    unittest.main()
