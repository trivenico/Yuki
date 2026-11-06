import io
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from yuki_cli import _reminder_request


class YukiReminderHttpClientTests(unittest.TestCase):
    def test_missing_session_token_fails_before_network_access(self):
        with patch.dict("os.environ", {}, clear=True), patch("yuki_cli.urlopen") as open_url:
            result, status = _reminder_request("/v1/companion/reminders")
        self.assertEqual(status, 2)
        self.assertIn("YUKI_ACCESS_TOKEN", result["error"])
        open_url.assert_not_called()

    def test_request_is_loopback_only_and_uses_bearer_auth(self):
        response = io.BytesIO(b'{"reminders":[]}')
        response.status = 200
        response.__enter__ = lambda self: self
        response.__exit__ = lambda self, *_args: False
        with patch("yuki_cli.urlopen", return_value=response) as open_url:
            result, status = _reminder_request(
                "/v1/companion/reminders", token="x" * 32,
            )
        self.assertEqual(status, 0)
        self.assertEqual(result, {"reminders": []})
        request = open_url.call_args.args[0]
        self.assertEqual(request.full_url, "http://127.0.0.1:8765/v1/companion/reminders")
        self.assertEqual(request.get_header("Authorization"), "Bearer " + "x" * 32)

    def test_expired_session_returns_sanitized_auth_error(self):
        error = HTTPError(
            "http://127.0.0.1:8765/v1/companion/reminders",
            401, "Unauthorized", {}, io.BytesIO(b"secret response"),
        )
        with patch("yuki_cli.urlopen", side_effect=error):
            result, status = _reminder_request(
                "/v1/companion/reminders", token="x" * 32,
            )
        self.assertEqual(status, 3)
        self.assertIn("invalid or expired", result["error"])
        self.assertNotIn("secret response", result["error"])


if __name__ == "__main__":
    unittest.main()
