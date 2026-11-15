import unittest
from unittest.mock import patch

from core.readiness import _probe_ollama, build_readiness_report
from core.component_catalog import DEFAULT_COMPONENTS
from core.component_registry import ComponentRegistry, ComponentRuntime


class RuntimeReadinessTests(unittest.TestCase):
    def test_ollama_probe_requires_literal_loopback_ip(self):
        calls = []
        def opener(request, timeout):
            calls.append(request.full_url)
            raise AssertionError("network probe should not run")
        for endpoint in ("http://localhost:11434", "http://ollama:11434", "http://192.0.2.10:11434"):
            with self.subTest(endpoint=endpoint):
                result = _probe_ollama(endpoint, opener=opener)
                self.assertEqual(result.status, "unavailable")
        self.assertEqual(calls, [])

    def test_ollama_probe_disables_environment_proxies(self):
        class Response:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *_): return False
            def read(self, _): return b"{}"
        class Opener:
            def open(self, request, timeout):
                self.request = request
                self.timeout = timeout
                return Response()
        opener = Opener()
        with patch("core.readiness.build_opener", return_value=opener) as build:
            result = _probe_ollama("http://127.0.0.1:11434", timeout=1.5)
        self.assertEqual(result.status, "verified")
        self.assertEqual(opener.timeout, 1.5)
        proxy_handler = build.call_args.args[0]
        self.assertEqual(proxy_handler.proxies, {})

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

    def test_readiness_validates_canonical_component_architecture(self):
        report = build_readiness_report(
            environ={},
            executable_finder=lambda name: None,
            module_finder=lambda name: None,
        )
        architecture = report.capabilities["architecture_catalog"]
        self.assertEqual(architecture.status, "verified")
        self.assertIn("component contracts", architecture.detail)

    def test_component_runtime_readiness_requires_started_healthy_registry(self):
        report = build_readiness_report(
            environ={}, executable_finder=lambda name: None,
            module_finder=lambda name: None,
        )
        self.assertEqual(report.capabilities["component_runtime"].status, "unconfigured")

        contract = next(item for item in DEFAULT_COMPONENTS if item.component_id == "identity-access")
        registry = ComponentRegistry(contracts=(contract,))
        registry.register(ComponentRuntime(
            contract=contract, instance=object(), start=lambda: None,
            close=lambda: None, health_check=lambda: True,
        ))
        registry.start_all()
        healthy = build_readiness_report(
            environ={}, executable_finder=lambda name: None,
            module_finder=lambda name: None, component_registry=registry,
        )
        self.assertEqual(healthy.capabilities["component_runtime"].status, "verified")

    def test_component_runtime_readiness_fails_closed_on_unhealthy_or_raising_probe(self):
        contract = next(item for item in DEFAULT_COMPONENTS if item.component_id == "identity-access")
        for probe in (lambda: False, lambda: (_ for _ in ()).throw(OSError("secret"))):
            registry = ComponentRegistry(contracts=(contract,))
            registry.register(ComponentRuntime(
                contract=contract, instance=object(), start=lambda: None,
                close=lambda: None, health_check=probe,
            ))
            registry.start_all()
            report = build_readiness_report(
                environ={}, executable_finder=lambda name: None,
                module_finder=lambda name: None, component_registry=registry,
            )
            self.assertEqual(report.capabilities["component_runtime"].status, "unavailable")
            self.assertNotIn("secret", report.capabilities["component_runtime"].detail)

    def test_empty_environment_reports_model_unconfigured(self):
        report = build_readiness_report(
            environ={},
            executable_finder=lambda name: None,
            module_finder=lambda name: None,
        )
        self.assertEqual(report.capabilities["ollama_model_config"].status, "unconfigured")


if __name__ == "__main__":
    unittest.main()
