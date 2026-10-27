import json
import os
import unittest
from unittest.mock import patch

from providers.hybrid import HybridProvider
from providers.ollama import OllamaProvider
from providers.openai_compatible import OpenAICompatibleProvider


class FakeProvider:
    def __init__(self, value="ok", available=True):
        self.value = value
        self.available = available
        self.calls = []

    def is_available(self):
        return self.available

    def generate(self, prompt):
        self.calls.append(prompt)
        return self.value


class Response:
    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, size=-1):
        return self.body[:size]


class HybridProviderTests(unittest.TestCase):
    def test_defaults_to_local_and_only_named_tasks_route_to_cloud(self):
        local, cloud = FakeProvider("local"), FakeProvider("cloud")
        router = HybridProvider(local, cloud, cloud_tasks={"summarize_public_page"})
        self.assertEqual(router.generate("private notes"), "local")
        self.assertEqual(router.generate_for("summarize_public_page", "public text"), "cloud")
        self.assertEqual(local.calls, ["private notes"])
        self.assertEqual(cloud.calls, ["public text"])

    def test_never_falls_back_to_cloud_when_local_is_unavailable(self):
        local, cloud = FakeProvider(available=False), FakeProvider()
        router = HybridProvider(local, cloud, cloud_tasks={"cloud_task"})
        with self.assertRaisesRegex(RuntimeError, "fallback is disabled"):
            router.generate_for("private_task", "private")
        self.assertEqual(cloud.calls, [])

    def test_cloud_task_requires_configured_available_provider(self):
        router = HybridProvider(FakeProvider(), None, cloud_tasks={"cloud_task"})
        with self.assertRaisesRegex(RuntimeError, "no cloud provider"):
            router.generate_for("cloud_task", "text")

    def test_rejects_insecure_cloud_endpoint_and_remote_plain_http_local_endpoint(self):
        with self.assertRaises(ValueError):
            OpenAICompatibleProvider("model", base_url="http://example.com/v1")
        with self.assertRaises(ValueError):
            OllamaProvider("http://example.com:11434", "model")

    def test_ollama_parses_response_and_bounds_request(self):
        provider = OllamaProvider(model="local-model")
        with patch("urllib.request.build_opener") as mocked_builder:
            mocked_builder.return_value.open.return_value = Response(
                json.dumps({"response": "hello"}).encode()
            )
            self.assertEqual(provider.generate("hi"), "hello")
        request = mocked_builder.return_value.open.call_args.args[0]
        self.assertEqual(request.full_url, "http://127.0.0.1:11434/api/generate")
        self.assertEqual(json.loads(request.data)["prompt"], "hi")

    def test_ollama_availability_requires_installed_model(self):
        provider = OllamaProvider(model="local-model")
        with patch("urllib.request.build_opener") as mocked_builder:
            mocked_builder.return_value.open.return_value = Response(
                json.dumps({"models": [{"name": "local-model:latest"}]}).encode()
            )
            self.assertTrue(provider.is_available())
        with patch("urllib.request.build_opener") as mocked_builder:
            mocked_builder.return_value.open.return_value = Response(
                json.dumps({"models": [{"name": "different-model:latest"}]}).encode()
            )
            self.assertFalse(provider.is_available())
        with patch("urllib.request.build_opener") as mocked_builder:
            mocked_builder.return_value.open.side_effect = OSError("offline")
            self.assertFalse(provider.is_available())

    def test_cloud_provider_uses_env_key_and_parses_response(self):
        provider = OpenAICompatibleProvider("cloud-model")
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-secret"}):
            with patch("urllib.request.build_opener") as mocked_builder:
                mocked_builder.return_value.open.return_value = Response(
                    json.dumps({"choices": [{"message": {"content": "answer"}}]}).encode()
                )
                self.assertTrue(provider.is_available())
                self.assertEqual(provider.generate("question"), "answer")
        request = mocked_builder.return_value.open.call_args.args[0]
        self.assertIn("Bearer test-secret", request.get_header("Authorization"))
        self.assertEqual(mocked_builder.return_value.open.call_args.kwargs["timeout"], provider.timeout)

    def test_cloud_provider_missing_key_fails_closed(self):
        provider = OpenAICompatibleProvider("cloud-model", api_key_env="YUKI_TEST_MISSING_KEY")
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(provider.is_available())
            with self.assertRaisesRegex(RuntimeError, "not configured"):
                provider.generate("question")


if __name__ == "__main__":
    unittest.main()
