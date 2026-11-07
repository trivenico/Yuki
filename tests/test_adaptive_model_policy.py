import unittest

from providers.adaptive_model_policy import assess_installed_models_for_device
from providers.ollama_model_inventory import InstalledModel

GIB = 1024 ** 3
MIB = 1024 ** 2


class AdaptiveModelPolicyTests(unittest.TestCase):
    def setUp(self):
        self.hardware = {
            "total_memory_bytes": 8 * GIB,
            "available_memory_bytes": 6 * GIB,
            "logical_cpu_count": 4,
            "gpu": {"detected": False, "memory_bytes": None},
        }
        self.models = [
            InstalledModel("compact:Q4", "a" * 64, 500 * MIB, "1B", "Q4_K_M", "llama"),
            InstalledModel("large:Q4", "b" * 64, 4 * GIB, "7B", "Q4_K_M", "llama"),
        ]

    def test_8gb_cpu_only_admits_only_model_within_budget(self):
        report = assess_installed_models_for_device(self.models, self.hardware)
        self.assertEqual(report["device_profile"]["execution_mode"], "cpu_only")
        self.assertEqual(
            [item["model_name"] for item in report["eligible_models"]],
            ["compact:Q4"],
        )
        self.assertEqual(
            report["excluded_models"][0]["blocker"],
            "model_exceeds_device_runtime_budget",
        )

    def test_gpu_is_optional_and_does_not_become_required(self):
        report = assess_installed_models_for_device(
            self.models, self.hardware, gpu_available_memory_bytes=2 * GIB
        )
        self.assertEqual(
            report["device_profile"]["execution_mode"], "gpu_accelerated_available"
        )
        self.assertFalse(report["device_profile"]["gpu_required"])

    def test_low_available_memory_excludes_models(self):
        hardware = dict(self.hardware, available_memory_bytes=256 * MIB)
        report = assess_installed_models_for_device(self.models, hardware)
        self.assertEqual(report["eligible_models"], [])
        self.assertTrue(all(
            item["blocker"] == "device_memory_budget_too_low"
            for item in report["excluded_models"]
        ))

    def test_never_loads_or_activates_models(self):
        report = assess_installed_models_for_device(self.models, self.hardware)
        self.assertFalse(report["automatic_load"])
        self.assertFalse(report["automatic_activation"])
        self.assertTrue(all(not item["automatic_load"] for item in report["eligible_models"]))

    def test_rejects_invalid_inventory(self):
        with self.assertRaises(ValueError):
            assess_installed_models_for_device(["not-a-model"], self.hardware)


if __name__ == "__main__":
    unittest.main()
