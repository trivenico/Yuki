import contextlib
import io
import json
import unittest
from unittest.mock import patch

from tools import provider_smoke_test


class FakeProvider:
    def __init__(self, response="SENSITIVE RESPONSE CONTENT", available=True):
        self.response = response
        self.available = available
        self.calls = 0

    def is_available(self):
        return self.available

    def generate(self, prompt):
        self.calls += 1
        return self.response


class ProviderSmokeCLITests(unittest.TestCase):
    def test_cli_refuses_live_request_without_explicit_confirmation(self):
        with patch("providers.factory.create_provider") as factory:
            with self.assertRaises(SystemExit) as exc:
                provider_smoke_test.main(["--json"])
        self.assertEqual(exc.exception.code, 2)
        factory.assert_not_called()

    def test_confirmed_json_cli_never_prints_model_response(self):
        provider = FakeProvider()
        stdout = io.StringIO()
        with patch("core.config.load_config", return_value={"provider": "gemini"}), \
             patch("providers.factory.create_provider", return_value=provider), \
             contextlib.redirect_stdout(stdout):
            result = provider_smoke_test.main(["--confirm-live-request", "--json"])
        self.assertEqual(result, 0)
        self.assertEqual(provider.calls, 1)
        output = stdout.getvalue()
        self.assertNotIn("SENSITIVE RESPONSE CONTENT", output)
        report = json.loads(output)
        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["provider"], "gemini")
        self.assertGreater(report["response_characters"], 0)

    def test_confirmed_cli_returns_nonzero_for_unavailable_provider(self):
        provider = FakeProvider(available=False)
        stdout = io.StringIO()
        with patch("core.config.load_config", return_value={"provider": "gemini"}), \
             patch("providers.factory.create_provider", return_value=provider), \
             contextlib.redirect_stdout(stdout):
            result = provider_smoke_test.main(["--confirm-live-request", "--json"])
        self.assertEqual(result, 1)
        self.assertEqual(provider.calls, 0)
        self.assertEqual(json.loads(stdout.getvalue())["status"], "fail")


if __name__ == "__main__":
    unittest.main()
