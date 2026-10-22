import json
import os
import unittest
from unittest.mock import patch

from providers.hybrid import HybridProvider
from providers.ollama import OllamaProvider
from providers.openai_compatible import OpenAICompatibleProvider, RetryableProviderError
from providers.failover import FailoverProvider


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


class FailoverProviderTests(unittest.TestCase):
    def test_uses_next_model_after_retryable_capacity_failure(self):
        class Model:
            def __init__(self, model, **kwargs):
                self.model = model
                self.calls = 0
                self.last_context_budget = None
            def is_available(self):
                return True
            def generate(self, prompt):
                self.calls += 1
                if self.model == "first":
                    raise RetryableProviderError("rate limited")
                return "completed by fallback"

        made = []
        def factory(model, **kwargs):
            item = Model(model, **kwargs)
            made.append(item)
            return item
        router = FailoverProvider(["first", "second"], provider_factory=factory)
        self.assertEqual(router.generate("continue work"), "completed by fallback")
        self.assertEqual([item.calls for item in made], [1, 1])
        self.assertEqual(router.last_model, "second")
        self.assertIsNone(router.last_error)

    def test_does_not_fallback_on_non_retryable_auth_or_request_error(self):
        class Model:
            def __init__(self, model, **kwargs):
                self.model = model
                self.calls = 0
            def is_available(self):
                return True
            def generate(self, prompt):
                self.calls += 1
                raise RuntimeError("invalid API key")
        made = []
        def factory(model, **kwargs):
            item = Model(model, **kwargs)
            made.append(item)
            return item
        router = FailoverProvider(["first", "second"], provider_factory=factory)
        with self.assertRaisesRegex(RuntimeError, "invalid API key"):
            router.generate("prompt")
        self.assertEqual([item.calls for item in made], [1, 0])

    def test_requires_nonempty_deduplicated_model_list(self):
        with self.assertRaises(ValueError):
            FailoverProvider([])
        router = FailoverProvider(["a", "a", "b"], provider_factory=lambda model, **kw: type(
            "Model", (), {"model": model, "is_available": lambda self: True, "generate": lambda self, p: "ok"}
        )())
        self.assertEqual(router.models, ("a", "b"))


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
        with patch("urllib.request.urlopen", return_value=Response(
            json.dumps({"response": "hello"}).encode()
        )) as mocked:
            self.assertEqual(provider.generate("hi"), "hello")
        request = mocked.call_args.args[0]
        self.assertEqual(request.full_url, "http://127.0.0.1:11434/api/generate")
        self.assertEqual(json.loads(request.data)["prompt"], "hi")


    def test_ollama_availability_requires_installed_model(self):
        provider = OllamaProvider(model="local-model")
        with patch("urllib.request.urlopen", return_value=Response(
            json.dumps({"models": [{"name": "local-model:latest"}]}).encode()
        )):
            self.assertTrue(provider.is_available())
        with patch("urllib.request.urlopen", return_value=Response(
            json.dumps({"models": [{"name": "different-model:latest"}]}).encode()
        )):
            self.assertFalse(provider.is_available())
        with patch("urllib.request.urlopen", side_effect=OSError("offline")):
            self.assertFalse(provider.is_available())

    def test_cloud_provider_uses_env_key_and_parses_response(self):
        provider = OpenAICompatibleProvider("cloud-model")
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-secret"}):
            with patch("urllib.request.urlopen", return_value=Response(
                json.dumps({"choices": [{"message": {"content": "answer"}}]}).encode()
            )) as mocked:
                self.assertTrue(provider.is_available())
                self.assertEqual(provider.generate("question"), "answer")
        self.assertIn("Bearer test-secret", mocked.call_args.args[0].get_header("Authorization"))

    def test_cloud_provider_missing_key_fails_closed(self):
        provider = OpenAICompatibleProvider("cloud-model", api_key_env="YUKI_TEST_MISSING_KEY")
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(provider.is_available())
            with self.assertRaisesRegex(RuntimeError, "not configured"):
                provider.generate("question")

    def test_context_budget_tracks_the_provider_used_for_latest_request(self):
        local, cloud = FakeProvider("local"), FakeProvider("cloud")
        local.last_context_budget = {
            "limit_tokens": 8192, "used_tokens": 100, "remaining_tokens": 8092,
            "estimated": False, "available": True,
        }
        cloud.last_context_budget = {
            "limit_tokens": 16384, "used_tokens": 200, "remaining_tokens": 16184,
            "estimated": True, "available": True,
        }
        router = HybridProvider(local, cloud, cloud_tasks={"public_task"})
        router.generate("private")
        self.assertEqual(router.last_context_budget["limit_tokens"], 8192)
        router.generate_for("public_task", "public")
        self.assertEqual(router.last_context_budget["limit_tokens"], 16384)
        self.assertTrue(router.last_context_budget["estimated"])


if __name__ == "__main__":
    unittest.main()
