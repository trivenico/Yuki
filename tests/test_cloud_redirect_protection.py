import unittest
import urllib.error
import urllib.request
from unittest.mock import Mock, patch

from providers.openai_compatible import (
    OpenAICompatibleProvider,
    _open_pinned_https_request,
)


class RedirectProtectionTests(unittest.TestCase):
    def test_pinned_transport_rejects_redirect_without_following_it(self):
        request = urllib.request.Request(
            "https://api.openai.com/v1/chat/completions",
            data=b'{"prompt":"private"}',
            headers={"Authorization": "Bearer secret"},
            method="POST",
        )
        response = Mock()
        response.status = 302
        response.reason = "Found"
        response.headers = {"Location": "https://attacker.example/collect"}
        connection = Mock()
        connection.getresponse.return_value = response
        public_address = (
            __import__("socket").AF_INET,
            __import__("socket").SOCK_STREAM,
            __import__("socket").IPPROTO_TCP,
            ("8.8.8.8", 443),
        )
        with patch(
            "providers.openai_compatible._resolve_public_addresses",
            return_value=(public_address,),
        ), patch(
            "providers.openai_compatible._PinnedHTTPSConnection",
            return_value=connection,
        ):
            with self.assertRaises(urllib.error.HTTPError) as caught:
                with _open_pinned_https_request(request, 5):
                    self.fail("redirect response must not be yielded")
        self.assertEqual(caught.exception.code, 302)
        connection.request.assert_called_once()
        connection.getresponse.assert_called_once()
        response.close.assert_called_once()
        connection.close.assert_called_once()

    def test_redirect_error_does_not_leak_credentials_or_prompt(self):
        provider = OpenAICompatibleProvider("test-model")
        redirect = urllib.error.HTTPError(
            "https://api.openai.com/v1/chat/completions", 302, "Found",
            {"Location": "https://attacker.example/collect"}, None,
        )
        with patch.dict("os.environ", {"OPENAI_API_KEY": "do-not-leak"}):
            with patch(
                "providers.openai_compatible._open_pinned_https_request",
                side_effect=redirect,
            ) as mocked_request:
                with self.assertRaisesRegex(RuntimeError, "redirects are not followed") as ctx:
                    provider.generate("private prompt")
        self.assertNotIn("do-not-leak", str(ctx.exception))
        self.assertNotIn("private prompt", str(ctx.exception))
        mocked_request.assert_called_once()


if __name__ == "__main__":
    unittest.main()
