import json
import unittest
from unittest.mock import patch

from providers.colibri import ColibriProvider


class _Response:
    def __init__(self, payload):
        self.payload = payload
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def read(self, limit=-1):
        return self.payload[:limit]


class _Opener:
    def __init__(self, response):
        self.response = response
    def open(self, request, timeout=None):
        return self.response


class ColibriProviderTests(unittest.TestCase):
    def test_rejects_remote_endpoint(self):
        with self.assertRaises(ValueError):
            ColibriProvider("model", base_url="http://192.0.2.10:8000/v1")

    def test_rejects_https_endpoint_for_local_service(self):
        with self.assertRaises(ValueError):
            ColibriProvider("model", base_url="https://localhost:8000/v1")

    def test_is_available_checks_models_envelope(self):
        provider = ColibriProvider("model")
        payload = json.dumps({"data": [{"id": "model"}]}).encode()
        with patch("providers.colibri._local_opener", return_value=_Opener(_Response(payload))):
            self.assertTrue(provider.is_available())

    def test_generate_returns_openai_compatible_text(self):
        provider = ColibriProvider("model")
        payload = json.dumps({"choices": [{"message": {"content": "hello"}}]}).encode()
        with patch("providers.colibri.urllib.request.urlopen", return_value=_Response(payload)):
            self.assertEqual(provider.generate("say hello"), "hello")

    def test_generate_rejects_oversized_response(self):
        provider = ColibriProvider("model")
        payload = b" " * 2_000_001
        with patch("providers.colibri.urllib.request.urlopen", return_value=_Response(payload)):
            with self.assertRaises(RuntimeError):
                provider.generate("prompt")


if __name__ == "__main__":
    unittest.main()
