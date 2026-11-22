import json
import unittest
from unittest.mock import patch

from providers.ollama import OllamaProvider
from providers.openai_compatible import OpenAICompatibleProvider


class FakeResponse:
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self, _limit):
        return self.payload


class FakeOpener:
    def __init__(self, payload):
        self.response = FakeResponse(payload)

    def open(self, _request, timeout):
        self.timeout = timeout
        return self.response


class ProviderUsageMetadataTests(unittest.TestCase):
    def test_ollama_exposes_server_reported_exact_usage(self):
        payload = {
            "message": {"content": "hello", "tool_calls": []},
            "prompt_eval_count": 21,
            "eval_count": 8,
        }
        provider = OllamaProvider(model="local-test")
        with patch("providers.ollama.urllib.request.build_opener",
                   return_value=FakeOpener(payload)):
            result = provider.generate_chat(
                messages=[{"role": "user", "content": "hello"}],
                tools=[], tool_choice="none",
            )
        self.assertEqual(result["usage"], {
            "provider": "ollama", "model": "local-test",
            "input_tokens": 21, "output_tokens": 8, "estimated": False,
        })

    def test_ollama_rejects_malformed_usage_instead_of_guessing(self):
        provider = OllamaProvider(model="local-test")
        payload = {
            "message": {"content": "hello", "tool_calls": []},
            "prompt_eval_count": True, "eval_count": 8,
        }
        with patch("providers.ollama.urllib.request.build_opener",
                   return_value=FakeOpener(payload)):
            with self.assertRaisesRegex(RuntimeError, "invalid token usage"):
                provider.generate_chat(
                    messages=[{"role": "user", "content": "hello"}],
                    tools=[], tool_choice="none",
                )

    def test_openai_compatible_exposes_server_reported_exact_usage(self):
        payload = {
            "choices": [{
                "message": {"content": "hello", "tool_calls": []},
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 34, "completion_tokens": 13},
        }
        provider = OpenAICompatibleProvider(
            "test-model", environ={"OPENAI_API_KEY": "test-key"},
        )
        with patch("providers.openai_compatible.urllib.request.build_opener",
                   return_value=FakeOpener(payload)):
            result = provider.generate_chat(
                messages=[{"role": "user", "content": "hello"}],
                tools=[], tool_choice="none",
            )
        self.assertEqual(result["usage"], {
            "provider": "openai_compatible", "model": "test-model",
            "input_tokens": 34, "output_tokens": 13, "estimated": False,
        })

    def test_openai_compatible_rejects_malformed_usage_metadata(self):
        payload = {
            "choices": [{
                "message": {"content": "hello", "tool_calls": []},
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 1.5, "completion_tokens": 13},
        }
        provider = OpenAICompatibleProvider(
            "test-model", environ={"OPENAI_API_KEY": "test-key"},
        )
        with patch("providers.openai_compatible.urllib.request.build_opener",
                   return_value=FakeOpener(payload)):
            with self.assertRaisesRegex(RuntimeError, "invalid token usage"):
                provider.generate_chat(
                    messages=[{"role": "user", "content": "hello"}],
                    tools=[], tool_choice="none",
                )


if __name__ == "__main__":
    unittest.main()
