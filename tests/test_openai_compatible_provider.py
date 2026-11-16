import json
import unittest
from unittest.mock import MagicMock, patch

from providers.openai_compatible import OpenAICompatibleProvider


class OpenAICompatibleProviderTimeoutTests(unittest.TestCase):
    def test_request_uses_configured_timeout(self):
        provider = OpenAICompatibleProvider(
            "test-model", timeout=9, environ={"OPENAI_API_KEY": "secret"}
        )
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps({
            "choices": [{"message": {"content": "ok"}}]
        }).encode()
        with patch("providers.openai_compatible.urllib.request.build_opener") as factory:
            factory.return_value.open.return_value = response
            result = provider.generate("hello")
        self.assertEqual(result, "ok")
        self.assertEqual(factory.return_value.open.call_args.kwargs["timeout"], 9)

    def test_timeout_is_sanitized(self):
        provider = OpenAICompatibleProvider(
            "test-model", timeout=2, environ={"OPENAI_API_KEY": "secret"}
        )
        with patch("providers.openai_compatible.urllib.request.build_opener") as factory:
            factory.return_value.open.side_effect = TimeoutError("sensitive endpoint detail")
            with self.assertRaisesRegex(RuntimeError, "request failed"):
                provider.generate("hello")


if __name__ == "__main__":
    unittest.main()
