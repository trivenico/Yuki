import io
import json
import unittest
from unittest.mock import patch

from ui.desktop import YukiDesktopClient


class FakeResponse:
    def __init__(self, value):
        self.value = value.encode("utf-8") if isinstance(value, str) else value
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def read(self, size=-1): return self.value[:size]
    def __iter__(self): return iter(self.value.splitlines(keepends=True))


class DesktopClientTests(unittest.TestCase):
    def setUp(self):
        self.client = YukiDesktopClient()

    @patch("ui.desktop.urlopen")
    def test_login_keeps_bearer_token_in_memory(self, urlopen_mock):
        urlopen_mock.return_value = FakeResponse(json.dumps({
            "access_token":"opaque-token","user":{"username":"tester"}}))
        user = self.client.login("tester", "long-password")
        self.assertEqual(user["username"], "tester")
        self.assertEqual(self.client.token, "opaque-token")
        request = urlopen_mock.call_args.args[0]
        self.assertTrue(request.full_url.endswith("/v1/auth/login"))

    @patch("ui.desktop.urlopen")
    def test_model_catalog_and_provider_selection(self, urlopen_mock):
        self.client.token = "opaque-token"
        urlopen_mock.return_value = FakeResponse(json.dumps({"items":[{"name":"ollama"}]}))
        self.assertEqual(self.client.models()[0]["name"], "ollama")
        request = urlopen_mock.call_args.args[0]
        self.assertTrue(request.full_url.endswith("/v1/models"))
        urlopen_mock.return_value = FakeResponse(json.dumps({"selected":True,"provider":"ollama"}))
        result = self.client.select_model("ollama")
        self.assertTrue(result["selected"])
        request = urlopen_mock.call_args.args[0]
        self.assertTrue(request.full_url.endswith("/v1/models/select"))

    @patch("ui.desktop.urlopen")
    def test_stream_emits_incremental_events(self, urlopen_mock):
        self.client.token = "opaque-token"
        urlopen_mock.return_value = FakeResponse(
            'data: {"type":"delta","content":"Hello "}\n\n'
            'data: {"type":"delta","content":"Yuki"}\n\n'
            'data: {"type":"completed","content":""}\n\n'
        )
        events = []
        self.client.stream_message("ys_test_session_123", "hello", events.append)
        self.assertEqual([item["content"] for item in events[:2]], ["Hello ", "Yuki"])
        request = urlopen_mock.call_args.args[0]
        self.assertEqual(request.get_header("Authorization"), "Bearer opaque-token")
        self.assertEqual(request.get_header("Accept"), "text/event-stream")

    @patch("ui.desktop.urlopen")
    def test_logout_clears_token_even_if_request_fails(self, urlopen_mock):
        self.client.token = "opaque-token"
        urlopen_mock.side_effect = OSError("offline")
        with self.assertRaises(RuntimeError):
            self.client.logout()
        self.assertIsNone(self.client.token)

    def test_stream_requires_authentication(self):
        with self.assertRaises(PermissionError):
            self.client.stream_message("ys_test_session_123", "hello", lambda _event: None)


if __name__ == "__main__":
    unittest.main()
