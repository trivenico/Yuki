import unittest
import urllib.request

from providers.http_client import _RejectRedirects


class RejectRedirectsTests(unittest.TestCase):
    def test_redirect_handler_refuses_cross_host_redirect(self):
        handler = _RejectRedirects()
        request = urllib.request.Request("http://127.0.0.1:11434/api/generate")
        redirected = handler.redirect_request(
            request,
            fp=None,
            code=302,
            msg="Found",
            headers={},
            newurl="https://attacker.example/collect",
        )
        self.assertIsNone(redirected)

    def test_redirect_handler_refuses_same_host_redirect_too(self):
        handler = _RejectRedirects()
        request = urllib.request.Request("https://api.example.test/v1/chat/completions")
        redirected = handler.redirect_request(
            request,
            fp=None,
            code=307,
            msg="Temporary Redirect",
            headers={},
            newurl="https://api.example.test/other",
        )
        self.assertIsNone(redirected)


if __name__ == "__main__":
    unittest.main()
