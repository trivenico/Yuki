import tempfile
import time
import unittest
from pathlib import Path

from core.oauth_os_key_provider import OSKeyringOAuthKeyProvider, OAuthOSKeyStoreError
from core.oauth_runtime import build_oauth_credential_vault


class MemoryCredentialBackend:
    def __init__(self):
        self.values = {}

    def get_password(self, service, username):
        return self.values.get((service, username))

    def set_password(self, service, username, password):
        self.values[(service, username)] = password


class OAuthRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.temp.name) / "oauth.sqlite3")
        self.backend = MemoryCredentialBackend()
        self.provider = OSKeyringOAuthKeyProvider(service_name="runtime-test", backend=self.backend)

    def tearDown(self):
        self.temp.cleanup()

    def test_vault_factory_fails_until_explicit_key_initialization(self):
        with self.assertRaisesRegex(OAuthOSKeyStoreError, "not_initialized"):
            build_oauth_credential_vault(self.path, key_provider=self.provider)

    def test_vault_encrypts_and_recovers_credentials_across_runtime_restart(self):
        self.provider.initialize()
        vault = build_oauth_credential_vault(self.path, key_provider=self.provider)
        vault.store(
            owner_id="owner-1", provider_id="provider-1", account_id="account-1",
            access_token="access-token-123", refresh_token="refresh-token-456",
            expires_at=int(time.time()) + 3600, scopes=["read"],
        )
        restarted_provider = OSKeyringOAuthKeyProvider(
            service_name="runtime-test", backend=self.backend
        )
        restarted = build_oauth_credential_vault(self.path, key_provider=restarted_provider)
        credential = restarted.get(
            owner_id="owner-1", provider_id="provider-1", account_id="account-1"
        )
        self.assertEqual(credential.access_token, "access-token-123")
        self.assertEqual(credential.refresh_token, "refresh-token-456")

    def test_vault_uses_separate_stable_hmac_key(self):
        self.provider.initialize()
        first = self.provider.hmac_key()
        self.assertEqual(len(first), 32)
        self.assertEqual(first, OSKeyringOAuthKeyProvider(
            service_name="runtime-test", backend=self.backend
        ).hmac_key())


if __name__ == "__main__":
    unittest.main()
