import unittest

from core.readiness import build_readiness_report


class RuntimeReadinessTests(unittest.TestCase):
    def test_report_contains_platform_and_capability_states(self):
        report = build_readiness_report(
            environ={"YUKI_OLLAMA_MODEL": "example-model"},
            executable_finder=lambda name: None,
            module_finder=lambda name: None,
        )
        data = report.to_dict()
        self.assertTrue(data["python"])
        self.assertTrue(data["operating_system"])
        self.assertTrue(data["architecture"])
        self.assertEqual(data["capabilities"]["ollama_cli"]["status"], "unavailable")
        self.assertEqual(data["capabilities"]["ollama_model_config"]["status"], "configured")
        self.assertEqual(data["capabilities"]["pytorch"]["status"], "unavailable")
        self.assertEqual(data["capabilities"]["onnxruntime"]["status"], "unavailable")

    def test_report_never_claims_model_or_accelerator_is_verified(self):
        report = build_readiness_report(
            environ={"YUKI_OLLAMA_MODEL": "example-model"},
            executable_finder=lambda name: "/usr/bin/ollama",
            module_finder=lambda name: object(),
        ).to_dict()
        self.assertIn("not verified", report["capabilities"]["ollama_cli"]["detail"])
        self.assertIn("not verified", report["capabilities"]["ollama_model_config"]["detail"])
        self.assertIn("not verified", report["capabilities"]["pytorch"]["detail"])
        self.assertIn("not verified", report["capabilities"]["onnxruntime"]["detail"])

    def test_resource_inventory_reads_linux_memory_without_thresholds(self):
        from core.readiness import ResourceInventory

        inventory = ResourceInventory.detect(
            meminfo_reader=lambda: "MemTotal:       8192000 kB\\nMemAvailable:   4096000 kB\\n",
            cpu_counter=lambda: 4,
            operating_system="Linux",
        )
        self.assertEqual(inventory.logical_cpu_count, 4)
        self.assertEqual(inventory.memory_total_bytes, 8192000 * 1024)
        self.assertEqual(inventory.memory_available_bytes, 4096000 * 1024)
        self.assertEqual(inventory.memory_source, "linux_proc_meminfo")

    def test_resource_inventory_handles_missing_or_malformed_memory(self):
        from core.readiness import ResourceInventory

        inventory = ResourceInventory.detect(
            meminfo_reader=lambda: "MemTotal: not-a-number kB\\n",
            cpu_counter=lambda: None,
            operating_system="Linux",
        )
        self.assertIsNone(inventory.logical_cpu_count)
        self.assertIsNone(inventory.memory_total_bytes)
        self.assertIsNone(inventory.memory_available_bytes)
        self.assertEqual(inventory.memory_source, "unavailable")

    def test_empty_environment_reports_model_unconfigured(self):
        report = build_readiness_report(
            environ={},
            executable_finder=lambda name: None,
            module_finder=lambda name: None,
        )
        self.assertEqual(report.capabilities["ollama_model_config"].status, "unconfigured")


if __name__ == "__main__":
    unittest.main()
