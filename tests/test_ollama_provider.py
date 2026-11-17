import json
import unittest
from unittest.mock import MagicMock, patch

from providers.ollama import OllamaProvider


class OllamaProviderTimeoutTests(unittest.TestCase):
    def setUp(self):
        self.provider = OllamaProvider(model="test-model", timeout=6)

    def test_chat_request_uses_configured_timeout(self):
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps({
            "message": {"content": "ok"}
        }).encode()
        with patch("providers.ollama.urllib.request.urlopen", return_value=response) as open_url:
            result = self.provider.generate_chat("system", [{"role": "user", "content": "hello"}])
        self.assertEqual(result, "ok")
        self.assertEqual(open_url.call_args.kwargs["timeout"], 6)

    def test_timeout_is_sanitized(self):
        with patch("providers.ollama.urllib.request.urlopen", side_effect=TimeoutError("private endpoint")):
            with self.assertRaisesRegex(RuntimeError, "chat request failed"):
                self.provider.generate_chat("system", [{"role": "user", "content": "hello"}])


if __name__ == "__main__":
    unittest.main()
