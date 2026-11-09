import unittest

from core.resource_budget import (
    GPUResource, HardwareSnapshot, ResourcePolicy, plan_model_placement,
)
from providers.ollama_model_inventory import InstalledModel


GIB = 1024 ** 3


class ResourceBudgetTests(unittest.TestCase):
    def setUp(self):
        self.model = InstalledModel("tiny:Q4", None, 4 * GIB, "7B", "Q4_K_M", "llama")

    def test_cpu_fit_uses_reserves_and_safety_margin(self):
        hardware = HardwareSnapshot(16 * GIB, 12 * GIB)
        result = plan_model_placement(self.model, hardware)
        self.assertTrue(result["fit"])
        self.assertEqual(result["recommended_placement"], "cpu")
        self.assertFalse(result["automatic_activation"])

    def test_no_fit_when_available_ram_is_insufficient(self):
        hardware = HardwareSnapshot(8 * GIB, 2 * GIB)
        result = plan_model_placement(self.model, hardware)
        self.assertFalse(result["fit"])
        self.assertIsNone(result["recommended_placement"])

    def test_gpu_requires_explicit_ratio_and_real_free_memory(self):
        hardware = HardwareSnapshot(
            16 * GIB, 3 * GIB,
            (GPUResource("GPU 0", 12 * GIB, "nvidia"),),
        )
        without_ratio = plan_model_placement(self.model, hardware)
        self.assertFalse(without_ratio["fit"])
        with_gpu = plan_model_placement(self.model, hardware, gpu_offload_ratio=1.0)
        self.assertEqual(with_gpu["recommended_placement"], "gpu")
        self.assertEqual(with_gpu["choices"][0]["gpu_name"], "GPU 0")

    def test_hybrid_requires_both_budgets(self):
        hardware = HardwareSnapshot(
            16 * GIB, 5 * GIB,
            (GPUResource("AMD Radeon", 4 * GIB, "amd"),),
        )
        result = plan_model_placement(self.model, hardware, gpu_offload_ratio=0.5)
        self.assertTrue(result["fit"])
        self.assertEqual(result["recommended_placement"], "hybrid")

    def test_gpu_reserve_is_enforced(self):
        hardware = HardwareSnapshot(
            16 * GIB, 3 * GIB,
            (GPUResource("small GPU", 1 * GIB, "unknown"),),
        )
        result = plan_model_placement(self.model, hardware, gpu_offload_ratio=1.0)
        self.assertFalse(result["fit"])

    def test_invalid_telemetry_and_ratios_are_rejected(self):
        with self.assertRaises(ValueError):
            HardwareSnapshot(4 * GIB, 5 * GIB)
        with self.assertRaises(ValueError):
            GPUResource("bad", True)
        with self.assertRaises(ValueError):
            plan_model_placement(
                self.model, HardwareSnapshot(8 * GIB, 6 * GIB),
                gpu_offload_ratio=1.5,
            )

    def test_policy_reserves_are_validated(self):
        with self.assertRaises(ValueError):
            ResourcePolicy(host_reserve_bytes=-1)
        with self.assertRaises(ValueError):
            ResourcePolicy(safety_margin_ratio=3)


if __name__ == "__main__":
    unittest.main()
