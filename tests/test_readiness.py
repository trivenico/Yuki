import unittest

from core.readiness import build_readiness_report


class RuntimeReadinessTests(unittest.TestCase):
    def test_report_contains_platform_resources_and_capability_states(self):
        report = build_readiness_report(
            environ={"YUKI_OLLAMA_MODEL": "example-model"},
            executable_finder=lambda name: None,
            module_finder=lambda name: None,
        )
        data = report.to_dict()
        self.assertTrue(data["python"])
        self.assertTrue(data["operating_system"])
        self.assertTrue(data["architecture"])
        self.assertIn("resources", data)
        self.assertIn("logical_cpu_count", data["resources"])
        self.assertIn("memory_total_bytes", data["resources"])
        self.assertIn("memory_available_bytes", data["resources"])
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
            meminfo_reader=lambda: "MemTotal:       8192000 kB\nMemAvailable:   4096000 kB\n",
            cpu_counter=lambda: 4,
            operating_system="Linux",
        )
        self.assertEqual(inventory.logical_cpu_count, 4)
        self.assertEqual(inventory.memory_total_bytes, 8192000 * 1024)
        self.assertEqual(inventory.memory_available_bytes, 4096000 * 1024)
        self.assertEqual(inventory.memory_source, "linux_proc_meminfo")
        self.assertEqual(inventory.memory_effective_available_bytes, 4096000 * 1024)

    def test_resource_inventory_handles_missing_or_malformed_memory(self):
        from core.readiness import ResourceInventory

        inventory = ResourceInventory.detect(
            meminfo_reader=lambda: "MemTotal: not-a-number kB\n",
            cpu_counter=lambda: None,
            operating_system="Linux",
        )
        self.assertIsNone(inventory.logical_cpu_count)
        self.assertIsNone(inventory.memory_total_bytes)
        self.assertIsNone(inventory.memory_available_bytes)
        self.assertEqual(inventory.memory_source, "unavailable")

    def test_resource_inventory_does_not_assume_linux_memory_on_other_platforms(self):
        from core.readiness import ResourceInventory

        inventory = ResourceInventory.detect(
            meminfo_reader=lambda: self.fail("meminfo must not be read off Linux"),
            cpu_counter=lambda: 2,
            operating_system="Windows",
        )
        self.assertEqual(inventory.logical_cpu_count, 2)
        self.assertIsNone(inventory.memory_total_bytes)
        self.assertIsNone(inventory.memory_available_bytes)
        self.assertEqual(inventory.memory_source, "unavailable")

    def test_resource_inventory_rejects_non_positive_cpu_counts(self):
        from core.readiness import ResourceInventory

        for reported_count in (0, -1, None):
            with self.subTest(reported_count=reported_count):
                inventory = ResourceInventory.detect(
                    meminfo_reader=lambda: "",
                    cpu_counter=lambda: reported_count,
                    operating_system="Windows",
                )
                self.assertIsNone(inventory.logical_cpu_count)

    def test_resource_inventory_prefers_cgroup_v2_effective_limit(self):
        from core.readiness import ResourceInventory

        files = {
            "/sys/fs/cgroup/memory.max": "1073741824",
            "/sys/fs/cgroup/memory.current": "268435456",
        }
        inventory = ResourceInventory.detect(
            meminfo_reader=lambda: "MemTotal: 8388608 kB\nMemAvailable: 4194304 kB\n",
            cgroup_reader=lambda path: files.get(path),
            cpu_counter=lambda: 4,
            operating_system="Linux",
        )
        self.assertEqual(inventory.memory_total_bytes, 8388608 * 1024)
        self.assertEqual(inventory.memory_cgroup_limit_bytes, 1073741824)
        self.assertEqual(inventory.memory_cgroup_available_bytes, 805306368)
        self.assertEqual(inventory.memory_cgroup_source, "cgroup_v2")
        self.assertEqual(inventory.memory_effective_available_bytes, 805306368)

    def test_resource_inventory_caps_effective_cpu_by_cgroup_v2_quota(self):
        from core.readiness import ResourceInventory

        files = {
            "/sys/fs/cgroup/cpu.max": "150000 100000",
            "/sys/fs/cgroup/memory.max": "max",
            "/sys/fs/cgroup/memory.current": "0",
        }
        inventory = ResourceInventory.detect(
            meminfo_reader=lambda: "",
            cgroup_reader=lambda path: files.get(path),
            cpu_counter=lambda: 8,
            operating_system="Linux",
        )
        self.assertEqual(inventory.logical_cpu_count, 8)
        self.assertEqual(inventory.cpu_cgroup_limit, 1.5)
        self.assertEqual(inventory.cpu_cgroup_source, "cgroup_v2")
        self.assertEqual(inventory.effective_cpu_count, 2)

    def test_resource_inventory_handles_fractional_cpu_quota_and_v1(self):
        from core.readiness import ResourceInventory

        files = {
            "/sys/fs/cgroup/cpu.max": "max 100000",
            "/sys/fs/cgroup/cpu/cpu.cfs_quota_us": "50000",
            "/sys/fs/cgroup/cpu/cpu.cfs_period_us": "100000",
        }
        inventory = ResourceInventory.detect(
            meminfo_reader=lambda: "",
            cgroup_reader=lambda path: files.get(path),
            cpu_counter=lambda: 16,
            operating_system="Linux",
        )
        self.assertEqual(inventory.cpu_cgroup_limit, 0.5)
        self.assertEqual(inventory.cpu_cgroup_source, "cgroup_v1")
        self.assertEqual(inventory.effective_cpu_count, 1)

    def test_resource_inventory_ignores_unlimited_and_malformed_cpu_quotas(self):
        from core.readiness import ResourceInventory

        files = {
            "/sys/fs/cgroup/cpu.max": "max 100000",
            "/sys/fs/cgroup/cpu/cpu.cfs_quota_us": "-1",
            "/sys/fs/cgroup/cpu/cpu.cfs_period_us": "100000",
        }
        inventory = ResourceInventory.detect(
            meminfo_reader=lambda: "",
            cgroup_reader=lambda path: files.get(path),
            cpu_counter=lambda: 4,
            operating_system="Linux",
        )
        self.assertIsNone(inventory.cpu_cgroup_limit)
        self.assertEqual(inventory.effective_cpu_count, 4)
        self.assertEqual(inventory.cpu_cgroup_source, "unavailable")

    def test_resource_inventory_falls_back_to_cgroup_v1(self):
        from core.readiness import ResourceInventory

        files = {
            "/sys/fs/cgroup/memory.max": "max",
            "/sys/fs/cgroup/memory.current": "100",
            "/sys/fs/cgroup/memory/memory.limit_in_bytes": "536870912",
            "/sys/fs/cgroup/memory/memory.usage_in_bytes": "134217728",
        }
        inventory = ResourceInventory.detect(
            meminfo_reader=lambda: "",
            cgroup_reader=lambda path: files.get(path),
            cpu_counter=lambda: 2,
            operating_system="Linux",
        )
        self.assertEqual(inventory.memory_cgroup_limit_bytes, 536870912)
        self.assertEqual(inventory.memory_cgroup_available_bytes, 402653184)
        self.assertEqual(inventory.memory_cgroup_source, "cgroup_v1")
        self.assertEqual(inventory.memory_effective_available_bytes, 402653184)

    def test_resource_inventory_ignores_unlimited_or_malformed_cgroup_values(self):
        from core.readiness import ResourceInventory

        files = {
            "/sys/fs/cgroup/memory.max": "max",
            "/sys/fs/cgroup/memory.current": "not-a-number",
            "/sys/fs/cgroup/memory/memory.limit_in_bytes": str(1 << 62),
            "/sys/fs/cgroup/memory/memory.usage_in_bytes": "0",
        }
        inventory = ResourceInventory.detect(
            meminfo_reader=lambda: "",
            cgroup_reader=lambda path: files.get(path),
            cpu_counter=lambda: 1,
            operating_system="Linux",
        )
        self.assertIsNone(inventory.memory_cgroup_limit_bytes)
        self.assertIsNone(inventory.memory_cgroup_available_bytes)
        self.assertEqual(inventory.memory_cgroup_source, "unavailable")

    def test_empty_environment_reports_model_unconfigured(self):
        report = build_readiness_report(
            environ={},
            executable_finder=lambda name: None,
            module_finder=lambda name: None,
        )
        self.assertEqual(report.capabilities["ollama_model_config"].status, "unconfigured")


if __name__ == "__main__":
    unittest.main()
