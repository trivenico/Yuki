import unittest

from providers.agent_adapter import ProviderAgentAdapter
from providers.adapter_conformance import (
    AdapterConformanceError, inspect_provider_adapter,
)


class ConformanceProvider:
    def generate_chat(self, *, messages, tools, tool_choice):
        return {"text": "ok", "tool_calls": {"tool_calls": []}}


class ContextAndStreamProvider:
    def generate_chat(self, *, messages, tools, tool_choice, execution_context):
        execution_context.check()
        return {"text": "ok", "tool_calls": {"tool_calls": []}}

    def generate_chat_stream(self, *, messages, tools, tool_choice, execution_context):
        execution_context.check()
        yield {"type": "text_delta", "text": "ok"}


class AdapterConformanceTests(unittest.TestCase):
    def test_legacy_adapter_reports_only_structurally_detected_capabilities(self):
        report = inspect_provider_adapter(ProviderAgentAdapter(ConformanceProvider()))
        self.assertEqual(report["verification_level"], "structural_only")
        self.assertTrue(report["capabilities"]["chat"])
        self.assertFalse(report["capabilities"]["streaming_interface"])
        self.assertFalse(report["capabilities"]["cooperative_execution_context"])
        self.assertIn("does not prove", " ".join(report["limitations"]))

    def test_context_and_stream_interfaces_are_detected_separately(self):
        report = inspect_provider_adapter(ProviderAgentAdapter(ContextAndStreamProvider()))
        self.assertTrue(report["capabilities"]["chat"])
        self.assertTrue(report["capabilities"]["streaming_interface"])
        self.assertTrue(report["capabilities"]["cooperative_execution_context"])
        self.assertTrue(report["capabilities"]["streaming_execution_context"])

    def test_incompatible_provider_signature_fails_closed(self):
        class BadProvider:
            def generate_chat(self, prompt):
                return {"text": prompt}
        with self.assertRaises(AdapterConformanceError):
            inspect_provider_adapter(ProviderAgentAdapter.__new__(ProviderAgentAdapter).__class__ if False else type(
                "Adapter", (), {"provider": BadProvider()}
            )())

    def test_missing_provider_fails_closed(self):
        with self.assertRaises(AdapterConformanceError):
            inspect_provider_adapter(object())


if __name__ == "__main__":
    unittest.main()
