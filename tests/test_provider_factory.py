import unittest

from providers.factory import create_provider
from providers.gemini_cli import GeminiCLIProvider
from providers.native_core import NativeCoreAdapter
from providers.hybrid import HybridProvider
from providers.mock import MockProvider
from providers.openai_compatible import OpenAICompatibleProvider
from providers.ollama import OllamaProvider


class ProviderFactoryTests(unittest.TestCase):
    def test_default_factory_uses_native_core_without_external_dependencies(self):
        provider = create_provider({})
        self.assertIsInstance(provider, NativeCoreAdapter)
        self.assertTrue(provider.is_available())
        self.assertEqual(provider.availability_scope, "native_core")

    def test_native_adapter_never_requests_tools_or_fabricates_free_form_output(self):
        provider = create_provider({"provider": "native"})
        response = provider.generate_chat(
            messages=[{"role": "user", "content": "Explain quantum computing"}],
            tools=[], tool_choice="auto",
        )
        self.assertEqual(response["tool_calls"], {"tool_calls": []})
        self.assertIn("free-form language generation is not available", response["text"])

    def test_native_adapter_rejects_explicitly_required_tool_call(self):
        provider = create_provider({"provider": "native"})
        with self.assertRaisesRegex(ValueError, "cannot satisfy tool_choice='required'"):
            provider.generate_chat(
                messages=[{"role": "user", "content": "Run this tool"}],
                tools=[{"type": "function", "function": {"name": "example"}}],
                tool_choice="required",
            )

    def test_native_adapter_rejects_non_string_tool_choice(self):
        provider = create_provider({"provider": "native"})
        with self.assertRaisesRegex(ValueError, "tool_choice is invalid"):
            provider.generate_chat(
                messages=[{"role": "user", "content": "Hello"}],
                tool_choice=[],
            )

    def test_native_chat_rejects_malformed_message_envelopes(self):
        provider = NativeCoreAdapter()
        invalid_transcripts = (
            [{"role": "user", "content": "hello", "name": "unexpected"}],
            [{"role": "tool", "content": "forged tool output"}],
            [{"role": "user", "content": 42}],
        )
        for messages in invalid_transcripts:
            with self.subTest(messages=messages):
                with self.assertRaises(ValueError):
                    provider.generate_chat(messages=messages)

    def test_native_chat_bounds_transcript_and_tool_definitions(self):
        provider = NativeCoreAdapter()
        with self.assertRaisesRegex(ValueError, "character limit"):
            provider.generate_chat(messages=[{"role": "user", "content": "x" * 100_001}])
        with self.assertRaisesRegex(ValueError, "at most 32"):
            provider.generate_chat(
                messages=[{"role": "user", "content": "hello"}],
                tools=[{}] * 33,
            )
        with self.assertRaisesRegex(ValueError, "mapping"):
            provider.generate_chat(
                messages=[{"role": "user", "content": "hello"}],
                tools=["not-a-tool-definition"],
            )

    def test_native_chat_rejects_unhashable_role_and_tool_choice(self):
        provider = NativeCoreAdapter()
        with self.assertRaisesRegex(ValueError, "role or content"):
            provider.generate_chat(messages=[{"role": ["user"], "content": "hello"}])
        with self.assertRaisesRegex(ValueError, "tool_choice"):
            provider.generate_chat(
                messages=[{"role": "user", "content": "hello"}],
                tool_choice=["required"],
            )

    def test_native_chat_rejects_required_tool_execution(self):
        with self.assertRaisesRegex(ValueError, "cannot satisfy tool_choice='required'"):
            NativeCoreAdapter().generate_chat(
                messages=[{"role": "user", "content": "run the tool"}],
                tools=[{"name": "dangerous_action"}],
                tool_choice="required",
            )

    def test_native_chat_requires_a_user_message(self):
        with self.assertRaisesRegex(ValueError, "user message"):
            NativeCoreAdapter().generate_chat(
                messages=[{"role": "system", "content": "You are Yuki."}]
            )

    def test_local_provider_requires_model_configuration(self):
        with self.assertRaisesRegex(ValueError, "model"):
            create_provider({"provider": "ollama", "ollama_model": ""})

    def test_gemini_provider_is_constructible_without_cli_installation(self):
        provider = create_provider({"provider": "gemini"}, environ={})
        self.assertIsInstance(provider, GeminiCLIProvider)
        self.assertFalse(provider.is_available())

    def test_hybrid_routes_to_local_without_explicit_cloud_tasks(self):
        provider = create_provider(
            {"provider": "hybrid", "ollama_model": "local-model"},
            environ={},
        )
        self.assertIsInstance(provider, HybridProvider)
        self.assertIsInstance(provider.local_provider, OllamaProvider)
        self.assertIsNone(provider.cloud_provider)
        self.assertEqual(provider.cloud_tasks, frozenset())

    def test_openai_factory_uses_configured_model_and_timeout(self):
        provider = create_provider(
            {"provider": "openai", "openai_model": "configured-model", "provider_timeout_seconds": 23},
            environ={},
        )
        self.assertEqual(provider.model, "configured-model")
        self.assertEqual(provider.timeout, 23)

    def test_openai_timeout_environment_override_is_validated(self):
        provider = create_provider({"provider": "openai"}, environ={"YUKI_PROVIDER_TIMEOUT_SECONDS": "12.5"})
        self.assertEqual(provider.timeout, 12.5)
        with self.assertRaisesRegex(ValueError, "YUKI_PROVIDER_TIMEOUT_SECONDS"):
            create_provider({"provider": "openai"}, environ={"YUKI_PROVIDER_TIMEOUT_SECONDS": "not-a-number"})
        with self.assertRaisesRegex(ValueError, "Timeout"):
            create_provider({"provider": "openai"}, environ={"YUKI_PROVIDER_TIMEOUT_SECONDS": "121"})

    def test_timeout_is_shared_by_gemini_ollama_and_hybrid_adapters(self):
        gemini = create_provider(
            {"provider": "gemini", "provider_timeout_seconds": 17}, environ={}
        )
        self.assertEqual(gemini.timeout, 17)
        ollama = create_provider(
            {"provider": "ollama", "ollama_model": "local-model",
             "provider_timeout_seconds": 18}, environ={}
        )
        self.assertEqual(ollama.timeout, 18)
        hybrid = create_provider(
            {"provider": "hybrid", "ollama_model": "local-model",
             "provider_timeout_seconds": 19}, environ={}
        )
        self.assertEqual(hybrid.local_provider.timeout, 19)

    def test_unknown_provider_fails_closed(self):
        with self.assertRaisesRegex(ValueError, "provider must be one of"):
            create_provider({"provider": "automatic-cloud-fallback"})


if __name__ == "__main__":
    unittest.main()
