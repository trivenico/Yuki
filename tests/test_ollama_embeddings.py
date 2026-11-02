import json
import unittest
from unittest.mock import patch

from providers.ollama_embeddings import OllamaEmbeddingProvider


class _Response:
    def __init__(self, payload):
        self.payload = payload
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def read(self, limit):
        return self.payload[:limit]


class OllamaEmbeddingProviderTests(unittest.TestCase):
    def test_posts_to_local_embed_endpoint_and_returns_vector(self):
        payload = json.dumps({"embeddings": [[0.1, 0.2, 0.3]]}).encode()
        provider = OllamaEmbeddingProvider(model="nomic-embed-text")
        with patch("providers.ollama_embeddings.urllib.request.urlopen", return_value=_Response(payload)) as open_url:
            vector = provider("Yuki memory retrieval")
        self.assertEqual(vector, (0.1, 0.2, 0.3))
        request = open_url.call_args.args[0]
        self.assertEqual(request.full_url, "http://127.0.0.1:11434/api/embed")
        self.assertEqual(json.loads(request.data)["model"], "nomic-embed-text")

    def test_rejects_non_loopback_plain_http(self):
        with self.assertRaises(ValueError):
            OllamaEmbeddingProvider(base_url="http://example.com:11434", model="embed")

    def test_rejects_invalid_vectors(self):
        provider = OllamaEmbeddingProvider(model="embed")
        payload = json.dumps({"embeddings": [[float("nan")]]}).encode()
        with patch("providers.ollama_embeddings.urllib.request.urlopen", return_value=_Response(payload)):
            with self.assertRaises(RuntimeError):
                provider("hello")

    def test_rejects_oversized_input(self):
        provider = OllamaEmbeddingProvider(model="embed")
        with self.assertRaises(ValueError):
            provider("x" * 12001)


if __name__ == "__main__":
    unittest.main()