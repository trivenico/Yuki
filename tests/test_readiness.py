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

    def test_empty_environment_reports_model_unconfigured(self):
        report = build_readiness_report(
            environ={},
            executable_finder=lambda name: None,
            module_finder=lambda name: None,
        )
        self.assertEqual(report.capabilities["ollama_model_config"].status, "unconfigured")


    def test_hardware_profile_reports_cpu_memory_disk_and_worker_guidance(self):
        report = build_readiness_report(
            environ={},
            executable_finder=lambda name: None,
            module_finder=lambda name: None,
        )
        capabilities = report.capabilities
        self.assertEqual(capabilities["cpu"].status, "detected")
        self.assertIn("logical CPU", capabilities["cpu"].detail)
        self.assertIn(capabilities["memory"].status, {"detected", "unknown"})
        self.assertIn(capabilities["workspace_disk"].status, {"detected", "unknown"})
        self.assertEqual(capabilities["recommended_worker_concurrency"].status, "recommended")
        self.assertIn("active worker", capabilities["recommended_worker_concurrency"].detail)

    def test_worker_recommendation_is_conservative_without_memory_measurement(self):
        from core.readiness import _worker_recommendation
        workers, guidance = _worker_recommendation(32, None)
        self.assertEqual(workers, 1)
        self.assertIn("could not be measured", guidance)

    def test_worker_recommendation_scales_with_memory_not_core_count_alone(self):
        from core.readiness import _worker_recommendation
        self.assertEqual(_worker_recommendation(32, 6 * 1024**3)[0], 1)
        self.assertEqual(_worker_recommendation(32, 12 * 1024**3)[0], 2)
        self.assertEqual(_worker_recommendation(32, 24 * 1024**3)[0], 3)
        self.assertEqual(_worker_recommendation(32, 64 * 1024**3)[0], 4)


if __name__ == "__main__":
    unittest.main()
