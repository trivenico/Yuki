import io
import unittest
from unittest.mock import patch
from providers.ollama import OllamaProvider

class FakeResponse:
    def __init__(self, lines):
        self.stream = io.BytesIO(b"".join(lines))
        self.closed = False
    def __enter__(self): return self
    def __exit__(self, *args): self.close()
    def readline(self, size=-1): return self.stream.readline(size)
    def close(self): self.closed = True

class OllamaStreamingTests(unittest.TestCase):
    def setUp(self): self.provider = OllamaProvider(model="test-model", timeout=2)
    @patch("providers.ollama.urllib.request.urlopen")
    def test_stream_yields_deltas_and_requires_done(self, mocked):
        response = FakeResponse([b'{"response":"Hi ","done":false}\n', b'{"response":"Yuki","done":true}\n'])
        mocked.return_value = response
        self.assertEqual(list(self.provider.stream("hello")), ["Hi ", "Yuki"])
        self.assertTrue(response.closed)
        self.assertIn(b'"stream": true', mocked.call_args.args[0].data)
    @patch("providers.ollama.urllib.request.urlopen")
    def test_truncated_stream_fails(self, mocked):
        mocked.return_value = FakeResponse([b'{"response":"partial","done":false}\n'])
        with self.assertRaisesRegex(RuntimeError, "before its completion"):
            list(self.provider.stream("hello"))
    @patch("providers.ollama.urllib.request.urlopen")
    def test_invalid_event_fails(self, mocked):
        mocked.return_value = FakeResponse([b"not-json\n"])
        with self.assertRaisesRegex(RuntimeError, "invalid streaming event"):
            list(self.provider.stream("hello"))
    @patch("providers.ollama.urllib.request.urlopen")
    def test_generator_close_closes_response(self, mocked):
        response = FakeResponse([b'{"response":"one","done":false}\n', b'{"response":"two","done":true}\n'])
        mocked.return_value = response
        iterator = self.provider.stream("hello")
        self.assertEqual(next(iterator), "one")
        iterator.close()
        self.assertTrue(response.closed)
    def test_empty_prompt_rejected(self):
        with self.assertRaises(ValueError): list(self.provider.stream(" "))
if __name__ == "__main__": unittest.main()
