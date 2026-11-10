import unittest
from types import SimpleNamespace

from core.oauth_refresh_service import OAuthRefreshError, OAuthTokenRefreshService


class Vault:
    def __init__(self, credential):
        self.credential = credential
        self.rotations = []
    def get(self, **kwargs):
        return self.credential
    def rotate(self, **kwargs):
        self.rotations.append(kwargs)
        return self.credential.version + 1


class Transport:
    def __init__(self, document=None, error=None):
        self.document, self.error, self.calls = document, error, []
    def request_form(self, endpoint, *, fields):
        self.calls.append((endpoint, fields))
        if self.error:
            raise self.error
        return self.document


class OAuthTokenRefreshServiceTests(unittest.TestCase):
    def setUp(self):
        self.clock = lambda: 1_800_000_000
        self.credential = SimpleNamespace(
            owner_id="owner-1", provider_id="provider-1", account_id="account-1",
            access_token="old-access", refresh_token="old-refresh",
            expires_at=1_800_000_010, scopes=("read",), version=4,
        )
        self.vault = Vault(self.credential)
        self.transport = Transport({
            "access_token": "new-access",
            "token_type": "Bearer",
            "expires_in": 3600,
            "scope": "read",
        })
        self.service = OAuthTokenRefreshService(
            credential_vault=self.vault, transport=self.transport,
            token_endpoint="https://auth.example.com/token",
            provider_id="provider-1", client_id="client-1",
            issuer="https://auth.example.com", resource="https://api.example.com",
            clock=self.clock, refresh_skew_seconds=120,
        )

    def test_fresh_credential_is_not_refreshed(self):
        self.credential.expires_at = 1_800_001_000
        result = self.service.refresh_if_needed(owner_id="owner-1", account_id="account-1")
        self.assertFalse(result["refreshed"])
        self.assertEqual(self.transport.calls, [])

    def test_refresh_rotates_vault_and_omitted_refresh_token_is_preserved(self):
        result = self.service.refresh_if_needed(owner_id="owner-1", account_id="account-1")
        self.assertTrue(result["refreshed"])
        self.assertEqual(result["credential_version"], 5)
        self.assertEqual(self.vault.rotations[0]["expected_refresh_token"], "old-refresh")
        self.assertIsNone(self.vault.rotations[0]["refresh_token"])
        self.assertEqual(self.transport.calls[0][1]["grant_type"], "refresh_token")
        self.assertNotIn("new-access", repr(result))

    def test_widened_scopes_are_rejected_before_vault_rotation(self):
        self.transport.document["scope"] = "read write"
        with self.assertRaises(OAuthRefreshError):
            self.service.refresh_if_needed(owner_id="owner-1", account_id="account-1")
        self.assertEqual(self.vault.rotations, [])

    def test_ambiguous_transport_failure_is_not_retried(self):
        self.transport.error = TimeoutError("provider rotated before timeout")
        with self.assertRaises(OAuthRefreshError):
            self.service.refresh_if_needed(owner_id="owner-1", account_id="account-1")
        self.assertEqual(len(self.transport.calls), 1)
        self.assertEqual(self.vault.rotations, [])

    def test_missing_refresh_token_fails_closed(self):
        self.credential.refresh_token = None
        with self.assertRaisesRegex(OAuthRefreshError, "refresh_credential_unavailable"):
            self.service.refresh_if_needed(owner_id="owner-1", account_id="account-1")
        self.assertEqual(self.transport.calls, [])


if __name__ == "__main__":
    unittest.main()
