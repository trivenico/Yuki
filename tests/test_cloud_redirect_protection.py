import unittest
from providers.openai_compatible import OpenAICompatibleProvider
from core.pinned_https_provider_transport import PinnedHTTPSProviderTransport, PinnedHTTPTransportError


class RejectingTransport:
    def open(self, request, timeout=None):
        raise PinnedHTTPTransportError("cloud_redirect_rejected")


class RedirectProtectionTests(unittest.TestCase):
    def test_provider_defaults_to_dns_pinned_transport(self):
        provider = OpenAICompatibleProvider("test-model")
        self.assertIsInstance(provider._transport, PinnedHTTPSProviderTransport)

    def test_redirect_error_does_not_leak_credentials_or_prompt(self):
        provider = OpenAICompatibleProvider(
            "test-model", transport=RejectingTransport(),
            environ={"OPENAI_API_KEY": "do-not-leak"},
        )
        with self.assertRaisesRegex(RuntimeError, "redirects are not followed") as ctx:
            provider.generate("private prompt")
        self.assertNotIn("do-not-leak", str(ctx.exception))
        self.assertNotIn("private prompt", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
