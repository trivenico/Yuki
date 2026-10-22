import unittest
from unittest.mock import patch
import urllib.error
from providers.openai_compatible import OpenAICompatibleProvider, _RejectRedirects

class RedirectProtectionTests(unittest.TestCase):
    def test_redirect_handler_refuses_redirect(self):
        handler = _RejectRedirects()
        request = __import__("urllib.request", fromlist=["Request"]).Request("https://api.openai.com/v1/chat/completions")
        self.assertIsNone(handler.redirect_request(request, None, 302, "Found", {}, "https://attacker.example/collect"))

    def test_redirect_error_does_not_leak_credentials_or_prompt(self):
        provider = OpenAICompatibleProvider("test-model")
        with patch.dict("os.environ", {"OPENAI_API_KEY": "do-not-leak"}):
            with patch("urllib.request.build_opener") as build_opener:
                build_opener.return_value.open.side_effect = urllib.error.HTTPError(
                    "https://api.openai.com/v1/chat/completions", 302, "Found",
                    {"Location": "https://attacker.example/collect"}, None
                )
                with self.assertRaisesRegex(RuntimeError, "redirects are not followed") as ctx:
                    provider.generate("private prompt")
        self.assertNotIn("do-not-leak", str(ctx.exception))
        self.assertNotIn("private prompt", str(ctx.exception))
        build_opener.return_value.open.assert_called_once()

if __name__ == "__main__":
    unittest.main()
