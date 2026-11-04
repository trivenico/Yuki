import unittest
from unittest.mock import patch

from providers.hardware_discovery import (
    GPUDevice,
    HardwareDiscovery,
    estimate_model_resources,
    inventory_to_dict,
)


class HardwareDiscoveryTests(unittest.TestCase):
    def test_read_only_inventory_reports_platform_and_cpu(self):
        result = HardwareDiscovery().discover()
        self.assertTrue(result.operating_system)
        self.assertTrue(result.architecture)
        self.assertGreaterEqual(result.logical_cpu_count, 1)
        self.assertEqual(result.inventory_scope, "read_only_local_inventory")
        self.assertEqual(result.gpu_detection, "not_detected")

    def test_gpu_probe_is_injected_and_sanitely_reported(self):
        gpu = GPUDevice("Test GPU", "Test Vendor", 8_000, 4_000, "test-runtime")
        result = HardwareDiscovery(gpu_probe=lambda: [gpu]).discover()
        self.assertEqual(result.gpus, (gpu,))
        self.assertEqual(result.gpu_detection, "runtime_probe")

    def test_gpu_probe_failure_does_not_break_inventory(self):
        def fail():
            raise RuntimeError("private driver details")
        result = HardwareDiscovery(gpu_probe=fail).discover()
        self.assertEqual(result.gpu_detection, "runtime_probe_failed")
        self.assertEqual(result.gpus, ())

    def test_gpu_probe_rejects_invalid_result(self):
        result = HardwareDiscovery(gpu_probe=lambda: ["not a device"]).discover()
        self.assertEqual(result.gpu_detection, "runtime_probe_failed")

    def test_storage_capacity_requires_existing_directory(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            result = HardwareDiscovery().discover(storage_path=directory)
            self.assertGreaterEqual(result.storage_free_bytes, 0)
            from pathlib import Path
            self.assertEqual(result.storage_path, str(Path(directory).resolve()))

    def test_inventory_serializes_without_device_identifiers(self):
        result = inventory_to_dict(HardwareDiscovery().discover())
        self.assertIn("physical_memory_bytes", result)
        self.assertNotIn("serial_number", result)
        self.assertNotIn("mac_address", result)

    def test_estimate_weights_only_when_kv_metadata_unknown(self):
        result = estimate_model_resources(model_parameters=7_000_000_000, quantization_bits=4)
        self.assertEqual(result.weight_bytes_estimate, int(7_000_000_000 * .5 * 1.15))
        self.assertIsNone(result.kv_cache_bytes_estimate)
        self.assertIsNone(result.total_memory_bytes_estimate)
        self.assertEqual(result.confidence, "low")

    def test_estimate_includes_kv_cache_when_dimensions_are_known(self):
        result = estimate_model_resources(
            model_parameters=7_000_000_000, quantization_bits=4,
            context_tokens=4096, layers=32, kv_heads=8, head_dimension=128,
            kv_bytes_per_element=2,
        )
        self.assertEqual(result.kv_cache_bytes_estimate, 2 * 4096 * 32 * 8 * 128 * 2)
        self.assertEqual(
            result.total_memory_bytes_estimate,
            result.weight_bytes_estimate + result.kv_cache_bytes_estimate,
        )
        self.assertEqual(result.confidence, "medium")

    def test_partial_kv_metadata_is_rejected(self):
        with self.assertRaises(ValueError):
            estimate_model_resources(
                model_parameters=1_000_000, quantization_bits=4, context_tokens=2048
            )

    def test_invalid_model_metadata_is_rejected(self):
        for params, bits in [(0, 4), (100, 0), (True, 4), (100, 65)]:
            with self.subTest(params=params, bits=bits), self.assertRaises(ValueError):
                estimate_model_resources(model_parameters=params, quantization_bits=bits)


if __name__ == "__main__":
    unittest.main()
