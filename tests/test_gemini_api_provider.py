import json
import unittest
from unittest.mock import MagicMock, patch

from providers.gemini_api import GeminiAPIProvider


class GeminiAPIProviderTests(unittest.TestCase):
    def test_structured_messages_keep_system_instruction_separate_and_map_roles(self):
        provider = GeminiAPIProvider("gemini-2.5-flash", environ={"GEMINI_API_KEY": "secret"})
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps({
            "candidates": [{"content": {"parts": [{"text": "Hello"}]}}]
        }).encode()
        with patch("providers.gemini_api.urllib.request.build_opener") as opener_factory:
            opener = opener_factory.return_value
            opener.open.return_value = response
            self.assertEqual(provider.generate_chat("trusted policy", [
                {"role": "user", "content": "Hi"},
                {"role": "assistant", "content": "Hello"},
                {"role": "user", "content": "Continue"},
            ]), "Hello")
        request = opener.open.call_args.args[0]
        payload = json.loads(request.data.decode())
        self.assertEqual(payload["systemInstruction"]["parts"][0]["text"], "trusted policy")
        self.assertEqual([item["role"] for item in payload["contents"]], ["user", "model", "user"])
        self.assertNotIn("trusted policy", payload["contents"][0]["parts"][0]["text"])
        self.assertEqual(request.get_header("X-goog-api-key"), "secret")

    def test_rejects_invalid_or_non_alternating_history_before_network(self):
        provider = GeminiAPIProvider("gemini-2.5-flash", environ={"GEMINI_API_KEY": "secret"})
        with patch("providers.gemini_api.urllib.request.build_opener") as opener:
            with self.assertRaisesRegex(ValueError, "alternate"):
                provider.generate_chat("system", [
                    {"role": "user", "content": "one"},
                    {"role": "user", "content": "two"},
                ])
            opener.assert_not_called()

    def test_missing_key_fails_closed(self):
        provider = GeminiAPIProvider("gemini-2.5-flash", environ={})
        with self.assertRaisesRegex(RuntimeError, "GEMINI_API_KEY"):
            provider.generate("hello")

    def test_invalid_model_and_timeout_are_rejected(self):
        with self.assertRaises(ValueError):
            GeminiAPIProvider("../other-model")
        with self.assertRaises(ValueError):
            GeminiAPIProvider("gemini-2.5-flash", timeout=0)

    def test_response_size_is_bounded(self):
        provider = GeminiAPIProvider("gemini-2.5-flash", environ={"GEMINI_API_KEY": "secret"})
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b"x" * 2_000_001
        with patch("providers.gemini_api.urllib.request.build_opener") as opener_factory:
            opener_factory.return_value.open.return_value = response
            with self.assertRaisesRegex(RuntimeError, "2 MB"):
                provider.generate("hello")


if __name__ == "__main__":
    unittest.main()
