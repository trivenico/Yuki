import unittest
from types import SimpleNamespace

from core.oauth_provider_credentials import (
    OAuthProviderCredentialError,
    OAuthProviderCredentialResolver,
)


class ConnectionRegistry:
    def __init__(self, record):
        self.record = record
    def get(self, **kwargs):
        return self.record


class CredentialVault:
    def __init__(self, record):
        self.record = record
    def get(self, **kwargs):
        return self.record


class OAuthProviderCredentialResolverTests(unittest.TestCase):
    def setUp(self):
        self.connection = SimpleNamespace(
            provider_id="google", account_id="account-1",
            scopes=("gmail.send", "gmail.readonly"),
        )
        self.credential = SimpleNamespace(
            owner_id="owner-1", provider_id="google", account_id="account-1",
            access_token="secret-access-token", expires_at=5000,
            scopes=("gmail.send", "gmail.readonly"), version=3,
        )
        self.resolver = OAuthProviderCredentialResolver(
            connection_registry=ConnectionRegistry(self.connection),
            credential_vault=CredentialVault(self.credential),
            clock=lambda: 1000,
        )

    def test_resolves_exact_owner_provider_account_and_scope(self):
        result = self.resolver.resolve(
            owner_id="owner-1", provider_id="google", account_id="account-1",
            required_scopes=("gmail.send",),
        )
        self.assertEqual(result.access_token, "secret-access-token")
        self.assertEqual(result.version, 3)
        self.assertNotIn("secret-access-token", repr(result))

    def test_missing_scope_fails_closed(self):
        with self.assertRaisesRegex(OAuthProviderCredentialError, "scope_not_granted"):
            self.resolver.resolve(
                owner_id="owner-1", provider_id="google", account_id="account-1",
                required_scopes=("gmail.modify",),
            )

    def test_registry_and_vault_scope_metadata_must_match(self):
        self.credential.scopes = ("gmail.send",)
        with self.assertRaisesRegex(OAuthProviderCredentialError, "scope_metadata_mismatch"):
            self.resolver.resolve(
                owner_id="owner-1", provider_id="google", account_id="account-1",
                required_scopes=("gmail.send",),
            )

    def test_account_identity_must_match_exact_request(self):
        self.credential.account_id = "another-account"
        with self.assertRaisesRegex(OAuthProviderCredentialError, "identity_mismatch"):
            self.resolver.resolve(
                owner_id="owner-1", provider_id="google", account_id="account-1",
                required_scopes=("gmail.send",),
            )

    def test_expired_and_near_expiry_credentials_are_rejected(self):
        self.credential.expires_at = 1060
        with self.assertRaisesRegex(OAuthProviderCredentialError, "unavailable"):
            self.resolver.resolve(
                owner_id="owner-1", provider_id="google", account_id="account-1",
                required_scopes=("gmail.send",),
            )

    def test_unknown_or_unregistered_account_is_rejected(self):
        self.connection = None
        self.resolver = OAuthProviderCredentialResolver(
            connection_registry=ConnectionRegistry(None),
            credential_vault=CredentialVault(self.credential),
            clock=lambda: 1000,
        )
        with self.assertRaisesRegex(OAuthProviderCredentialError, "not_connected"):
            self.resolver.resolve(
                owner_id="owner-1", provider_id="google", account_id="account-1",
                required_scopes=("gmail.send",),
            )

    def test_invalid_or_duplicate_scope_request_is_rejected(self):
        for scopes in ((), ("gmail.send", "gmail.send"), ("bad scope",)):
            with self.subTest(scopes=scopes), self.assertRaises(OAuthProviderCredentialError):
                self.resolver.resolve(
                    owner_id="owner-1", provider_id="google", account_id="account-1",
                    required_scopes=scopes,
                )


if __name__ == "__main__":
    unittest.main()
