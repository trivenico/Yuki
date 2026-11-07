import unittest

from providers.device_execution_profile import (
    DeviceProfileError,
    build_execution_profile,
)

GIB = 1024 ** 3


class DeviceExecutionProfileTests(unittest.TestCase):
    def snapshot(self, total=8 * GIB, available=6 * GIB, cpus=4):
        return {
            "total_memory_bytes": total,
            "available_memory_bytes": available,
            "logical_cpu_count": cpus,
            "gpu": {"detected": False, "memory_bytes": None},
        }

    def test_8gb_laptop_without_gpu_gets_cpu_only_compact_profile(self):
        profile = build_execution_profile(self.snapshot())
        self.assertEqual(profile["profile"], "compact_8gb")
        self.assertEqual(profile["execution_mode"], "cpu_only")
        self.assertTrue(profile["cpu_inference_supported"])
        self.assertFalse(profile["gpu_required"])
        self.assertEqual(profile["recommended_context_tokens"], 2048)
        self.assertEqual(profile["max_concurrent_agent_tasks"], 1)
        self.assertLessEqual(profile["model_runtime_memory_budget_bytes"], 8 * GIB * 0.35)

    def test_available_ram_limits_budget(self):
        profile = build_execution_profile(self.snapshot(available=2 * GIB))
        self.assertEqual(profile["model_runtime_memory_budget_bytes"], GIB)

    def test_gpu_is_optional_acceleration(self):
        profile = build_execution_profile(
            self.snapshot(), gpu_available_memory_bytes=4 * GIB
        )
        self.assertEqual(profile["execution_mode"], "gpu_accelerated_available")
        self.assertFalse(profile["gpu_required"])

    def test_16gb_and_expanded_tiers_scale_concurrency(self):
        standard = build_execution_profile(self.snapshot(16 * GIB, 12 * GIB, 8))
        expanded = build_execution_profile(self.snapshot(32 * GIB, 24 * GIB, 16))
        self.assertEqual(standard["profile"], "standard_16gb")
        self.assertEqual(standard["max_concurrent_agent_tasks"], 2)
        self.assertEqual(expanded["profile"], "expanded")
        self.assertEqual(expanded["max_concurrent_agent_tasks"], 4)

    def test_unknown_or_invalid_memory_fails_closed(self):
        with self.assertRaises(DeviceProfileError):
            build_execution_profile({"total_memory_bytes": None})
        with self.assertRaises(DeviceProfileError):
            build_execution_profile(self.snapshot(8 * GIB, 9 * GIB))

    def test_zero_free_memory_disables_inference_recommendation(self):
        profile = build_execution_profile(self.snapshot(8 * GIB, 1))
        self.assertEqual(profile["status"], "insufficient_free_memory")
        self.assertEqual(profile["model_runtime_memory_budget_bytes"], 0)

    def test_invalid_cpu_count_rejected(self):
        with self.assertRaises(DeviceProfileError):
            build_execution_profile(self.snapshot(cpus=0))


if __name__ == "__main__":
    unittest.main()
