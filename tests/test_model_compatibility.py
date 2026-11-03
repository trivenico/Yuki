import unittest

from providers.hardware_discovery import GPUDevice, HardwareInventory, ModelResourceEstimate
from providers.model_compatibility import ModelCompatibilityEngine, compatibility_to_dict


def inventory(*, ram=16_000, gpu=0, disk=100_000):
    gpus = (GPUDevice("GPU", "test", gpu, gpu, "test"),) if gpu else ()
    return HardwareInventory("TestOS", "1", "x86_64", "Test CPU", 8, ram, gpus,
                              "/models", disk)


def estimate(*, memory=8_000, disk=4_000):
    return ModelResourceEstimate(1_000_000, 4.0, memory, None, memory, disk,
                                 "medium", ("test assumption",))


class ModelCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.engine = ModelCompatibilityEngine(memory_reserve_ratio=0.20, disk_reserve_ratio=0.15)

    def test_gpu_fit_is_preferred(self):
        result = self.engine.evaluate(inventory(ram=32_000, gpu=20_000), estimate(),
                                      runtime_supported=True)
        self.assertEqual(result.status, "compatible_gpu")
        self.assertEqual(result.recommended_execution, "gpu")

    def test_cpu_fit_when_gpu_is_too_small(self):
        result = self.engine.evaluate(inventory(ram=20_000, gpu=2_000), estimate(),
                                      runtime_supported=True)
        self.assertEqual(result.status, "compatible_cpu")

    def test_hybrid_requires_explicit_runtime_support(self):
        device = inventory(ram=8_000, gpu=4_000)
        result = self.engine.evaluate(device, estimate(memory=10_000),
                                      runtime_supported=True, hybrid_supported=True)
        self.assertEqual(result.status, "compatible_hybrid")
        blocked = self.engine.evaluate(device, estimate(memory=10_000),
                                       runtime_supported=True, hybrid_supported=False)
        self.assertEqual(blocked.status, "incompatible")

    def test_unknown_runtime_is_warning_not_silent_approval(self):
        result = self.engine.evaluate(inventory(), estimate(), runtime_supported=None)
        self.assertEqual(result.status, "compatible_cpu")
        self.assertTrue(any("not been verified" in item for item in result.warnings))
        self.assertFalse(result.automatic_activation)

    def test_runtime_incompatibility_blocks_fit(self):
        result = self.engine.evaluate(inventory(gpu=30_000), estimate(),
                                      runtime_supported=False)
        self.assertEqual(result.status, "incompatible")
        self.assertIn("runtime_model_incompatibility", result.reasons)

    def test_insufficient_memory_metadata_is_indeterminate(self):
        device = HardwareInventory("OS", "1", "arm", "CPU", 4, None, ())
        result = self.engine.evaluate(device, estimate(), runtime_supported=True)
        self.assertEqual(result.status, "insufficient_metadata")

    def test_disk_reserve_is_enforced(self):
        result = self.engine.evaluate(inventory(disk=4_500), estimate(disk=4_000),
                                      runtime_supported=True)
        self.assertEqual(result.status, "incompatible")
        self.assertIn("insufficient_disk_capacity", result.reasons)

    def test_unknown_disk_capacity_is_reported(self):
        device = HardwareInventory("OS", "1", "arm", "CPU", 4, 16_000, ())
        result = self.engine.evaluate(device, estimate(), runtime_supported=True)
        self.assertEqual(result.status, "compatible_cpu")
        self.assertTrue(any("Free storage" in item for item in result.warnings))

    def test_multiple_gpus_are_not_assumed_combinable(self):
        device = HardwareInventory("OS", "1", "x86_64", "CPU", 8, 4_000,
                                   (GPUDevice("A", memory_total_bytes=8_000),
                                    GPUDevice("B", memory_total_bytes=8_000)))
        result = self.engine.evaluate(device, estimate(memory=10_000),
                                      runtime_supported=True, hybrid_supported=False)
        self.assertEqual(result.status, "incompatible")
        self.assertEqual(result.usable_gpu_memory_bytes, 6_400)

    def test_invalid_policies_and_inputs_are_rejected(self):
        for kwargs in ({"memory_reserve_ratio": -0.1}, {"disk_reserve_ratio": 0.8}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                ModelCompatibilityEngine(**kwargs)
        with self.assertRaises(TypeError):
            self.engine.evaluate({}, estimate(), runtime_supported=True)

    def test_report_serializes(self):
        result = self.engine.evaluate(inventory(), estimate(), runtime_supported=True)
        data = compatibility_to_dict(result)
        self.assertEqual(data["status"], "compatible_cpu")
        self.assertFalse(data["automatic_activation"])


if __name__ == "__main__":
    unittest.main()
