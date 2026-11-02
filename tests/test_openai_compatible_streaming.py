import io
import json
import unittest
from unittest.mock import patch
from providers.openai_compatible import OpenAICompatibleProvider


class FakeResponse:
    def __init__(self, lines):
        self.stream = io.BytesIO(b"".join(lines))
        self.closed = False
    def __enter__(self): return self
    def __exit__(self, *args): self.close()
    def readline(self, size=-1): return self.stream.readline(size)
    def close(self): self.closed = True


def sse(body):
    return b"data: " + json.dumps(body).encode() + b"\n\n"


class OpenAICompatibleStreamingTests(unittest.TestCase):
    def setUp(self):
        self.provider = OpenAICompatibleProvider(
            "test-model", environ={"TEST_API_KEY": "secret"},
            api_key_env="TEST_API_KEY",
        )

    @patch("providers.openai_compatible.urllib.request.build_opener")
    def test_stream_yields_sse_content_and_requires_done(self, build_opener):
        response = FakeResponse([
            b": keepalive\n",
            sse({"choices": [{"delta": {"role": "assistant"}, "index": 0}]}),
            sse({"choices": [{"delta": {"content": "Hello "}, "index": 0}]}),
            sse({"choices": [{"delta": {"content": "Yuki"}, "index": 0}]}),
            b"data: [DONE]\n\n",
        ])
        opener = build_opener.return_value
        opener.open.return_value = response
        self.assertEqual(list(self.provider.stream("hello")), ["Hello ", "Yuki"])
        self.assertTrue(response.closed)
        request = opener.open.call_args.args[0]
        self.assertIn(b'"stream": true', request.data)
        self.assertEqual(request.get_header("Authorization"), "Bearer secret")
        self.assertEqual(request.get_header("Accept"), "text/event-stream")

    @patch("providers.openai_compatible.urllib.request.build_opener")
    def test_usage_only_event_is_ignored(self, build_opener):
        response = FakeResponse([
            sse({"choices": [], "usage": {"total_tokens": 3}}),
            sse({"choices": [{"delta": {"content": "ok"}}]}),
            b"data: [DONE]\n\n",
        ])
        build_opener.return_value.open.return_value = response
        self.assertEqual(list(self.provider.stream("hello")), ["ok"])

    @patch("providers.openai_compatible.urllib.request.build_opener")
    def test_truncated_stream_fails(self, build_opener):
        response = FakeResponse([sse({"choices": [{"delta": {"content": "partial"}}]})])
        build_opener.return_value.open.return_value = response
        with self.assertRaisesRegex(RuntimeError, "before the \\[DONE\\] marker"):
            list(self.provider.stream("hello"))

    @patch("providers.openai_compatible.urllib.request.build_opener")
    def test_generator_close_closes_response(self, build_opener):
        response = FakeResponse([
            sse({"choices": [{"delta": {"content": "one"}}]}),
            b"data: [DONE]\n\n",
        ])
        build_opener.return_value.open.return_value = response
        iterator = self.provider.stream("hello")
        self.assertEqual(next(iterator), "one")
        iterator.close()
        self.assertTrue(response.closed)

    def test_missing_api_key_fails_before_network(self):
        provider = OpenAICompatibleProvider("test-model", environ={}, api_key_env="TEST_API_KEY")
        with self.assertRaisesRegex(RuntimeError, "TEST_API_KEY"):
            list(provider.stream("hello"))

    def test_empty_prompt_rejected(self):
        with self.assertRaises(ValueError):
            list(self.provider.stream(" "))


if __name__ == "__main__":
    unittest.main()
