import json
import os
import unittest
import threading
import time
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


    def test_hybrid_provider_propagates_local_usage_metadata(self):
        local = FakeProvider("local")
        local.last_usage = {"input_tokens": 2, "output_tokens": 1, "estimated": False}
        router = HybridProvider(local)
        self.assertEqual(router.generate("private"), "local")
        self.assertEqual(router.last_usage, local.last_usage)
        self.assertEqual(router.last_usage_provider, "fakeprovider")

    def test_hybrid_provider_propagates_cloud_usage_metadata(self):
        local, cloud = FakeProvider("local"), FakeProvider("cloud")
        cloud.last_usage = {"input_tokens": 7, "output_tokens": 4, "estimated": False}
        router = HybridProvider(local, cloud, cloud_tasks={"cloud_task"})
        self.assertEqual(router.generate_for("cloud_task", "public"), "cloud")
        self.assertEqual(router.last_usage, cloud.last_usage)
        self.assertEqual(router.last_usage_provider, "fakeprovider")


    def test_hybrid_routers_serialize_calls_to_shared_underlying_provider(self):
        class ConcurrentProvider(FakeProvider):
            def __init__(self):
                super().__init__()
                self.active = 0
                self.max_active = 0
                self.guard = threading.Lock()
                self.last_usage = None

            def generate(self, prompt):
                with self.guard:
                    self.active += 1
                    self.max_active = max(self.max_active, self.active)
                time.sleep(0.02)
                self.last_usage = {
                    "input_tokens": len(prompt), "output_tokens": len(prompt) + 1,
                    "estimated": False,
                }
                with self.guard:
                    self.active -= 1
                return prompt

        provider = ConcurrentProvider()
        routers = [HybridProvider(provider), HybridProvider(provider)]
        barrier = threading.Barrier(2)
        results = [None, None]

        def call(index):
            barrier.wait()
            answer = routers[index].generate("x" * (index + 2))
            results[index] = (answer, routers[index].last_usage)

        threads = [threading.Thread(target=call, args=(i,)) for i in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=2)
        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(provider.max_active, 1)
        self.assertEqual(results[0][1]["input_tokens"], 2)
        self.assertEqual(results[1][1]["input_tokens"], 3)

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
            json.dumps({"response": "hello", "prompt_eval_count": 4, "eval_count": 2}).encode()
        )) as mocked:
            self.assertEqual(provider.generate("hi"), "hello")
        self.assertEqual(provider.last_usage, {
            "input_tokens": 4, "output_tokens": 2, "estimated": False,
        })
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
            with patch("urllib.request.build_opener") as mocked_builder:
                mocked_builder.return_value.open.return_value = Response(
                    json.dumps({"choices": [{"message": {"content": "answer"}}],
                               "usage": {"prompt_tokens": 5, "completion_tokens": 3}}).encode()
                )
                self.assertTrue(provider.is_available())
                self.assertEqual(provider.generate("question"), "answer")
                self.assertEqual(provider.last_usage, {
                    "input_tokens": 5, "output_tokens": 3, "estimated": False,
                })
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
