import unittest
from core.oauth_provider_identity import OAuthProviderIdentityError, OAuthProviderIdentityVerifier

class Transport:
    def __init__(self, profile=None, error=None):
        self.profile, self.error, self.calls = profile, error, []
    def request_json(self, method, endpoint, *, headers):
        self.calls.append((method, endpoint, headers))
        if self.error:
            raise self.error
        return self.profile

class OAuthProviderIdentityVerifierTests(unittest.TestCase):
    def make_verifier(self, transport, **kwargs):
        return OAuthProviderIdentityVerifier(
            transport=transport,
            userinfo_endpoint="https://identity.example.com/userinfo",
            expected_subject="acct-123",
            expected_issuer="https://identity.example.com",
            **kwargs,
        )

    def test_validates_subject_and_sends_bearer_to_endpoint(self):
        transport = Transport({"sub":"acct-123", "name":"Owner"})
        result = self.make_verifier(transport).verify(access_token="opaque-token")
        self.assertEqual(result["subject"], "acct-123")
        self.assertEqual(transport.calls[0][0], "GET")
        self.assertEqual(transport.calls[0][2]["Authorization"], "Bearer opaque-token")

    def test_rejects_subject_mismatch(self):
        verifier = self.make_verifier(Transport({"sub":"other-account"}))
        with self.assertRaisesRegex(OAuthProviderIdentityError, "identity_mismatch"):
            verifier.verify(access_token="opaque-token")

    def test_rejects_issuer_mismatch(self):
        verifier = self.make_verifier(Transport({"sub":"acct-123", "iss":"https://evil.example"}))
        with self.assertRaisesRegex(OAuthProviderIdentityError, "issuer_mismatch"):
            verifier.verify(access_token="opaque-token")

    def test_optional_audience_is_enforced(self):
        verifier = self.make_verifier(
            Transport({"sub":"acct-123", "aud":"wrong-client"}),
            expected_audience="yuki-client",
        )
        with self.assertRaisesRegex(OAuthProviderIdentityError, "audience_mismatch"):
            verifier.verify(access_token="opaque-token")

    def test_transport_errors_are_sanitized(self):
        verifier = self.make_verifier(Transport(error=RuntimeError("secret token leaked")))
        with self.assertRaises(OAuthProviderIdentityError) as caught:
            verifier.verify(access_token="opaque-token")
        self.assertNotIn("secret token", str(caught.exception))

if __name__ == "__main__":
    unittest.main()
