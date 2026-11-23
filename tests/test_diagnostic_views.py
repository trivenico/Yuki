import unittest
from types import SimpleNamespace

from ui.diagnostic_views import render_diagnostics


class DiagnosticViewTests(unittest.TestCase):
    def test_report_renders_safe_capability_metadata(self):
        report = SimpleNamespace(capabilities={
            "python_runtime": SimpleNamespace(status="detected", detail="3.11"),
            "ollama_model_config": SimpleNamespace(status="unconfigured", detail="Set model name"),
        })
        rendered = render_diagnostics(
            readiness_builder=lambda: report,
            module_finder=lambda name: object() if name == "textual" else None,
        )
        self.assertIn("Yuki developer diagnostics", rendered)
        self.assertIn("Textual dashboard: available", rendered)
        self.assertIn("ollama_model_config: unconfigured", rendered)
        self.assertIn("read-only", rendered)

    def test_report_does_not_render_environment_values(self):
        report = SimpleNamespace(capabilities={
            "provider": SimpleNamespace(status="configured", detail="Provider configured"),
        })
        rendered = render_diagnostics(
            readiness_builder=lambda: report,
            module_finder=lambda name: None,
        )
        self.assertNotIn("API_KEY", rendered)
        self.assertNotIn("secret-value", rendered)


if __name__ == "__main__":
    unittest.main()
