import hashlib
import hmac
import tempfile
import unittest
from pathlib import Path

from core.oauth_credential_vault import OAuthCredentialError, SQLiteOAuthCredentialVault


class TestOnlyCipher:
    """Test-only authenticated transform; never use outside this test module."""

    def __init__(self):
        self.key = b"test-only-key-material-for-vault-tests"

    def _mask(self, associated_data, counter):
        return hmac.new(self.key, associated_data + b"\\0" + counter.to_bytes(4, "big"), hashlib.sha256).digest()

    def encrypt(self, plaintext, *, associated_data):
        ciphertext = bytearray()
        for counter, offset in enumerate(range(0, len(plaintext), 32)):
            mask = self._mask(associated_data, counter)
            ciphertext.extend(a ^ b for a, b in zip(plaintext[offset:offset + 32], mask))
        ciphertext = bytes(ciphertext)
        tag = hmac.new(self.key, associated_data + b"\\0" + ciphertext, hashlib.sha256).digest()
        return "test-key-v1", tag + ciphertext

    def decrypt(self, key_id, ciphertext, *, associated_data):
        if key_id != "test-key-v1" or len(ciphertext) < 32:
            raise ValueError("invalid test ciphertext")
        tag, encrypted = ciphertext[:32], ciphertext[32:]
        expected = hmac.new(self.key, associated_data + b"\\0" + encrypted, hashlib.sha256).digest()
        if not hmac.compare_digest(tag, expected):
            raise ValueError("authentication failed")
        plaintext = bytearray()
        for counter, offset in enumerate(range(0, len(encrypted), 32)):
            mask = self._mask(associated_data, counter)
            plaintext.extend(a ^ b for a, b in zip(encrypted[offset:offset + 32], mask))
        return bytes(plaintext)


class OAuthCredentialVaultTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = str(Path(self.tmp.name) / "credentials.sqlite3")
        self.cipher = TestOnlyCipher()
        self.vault = SQLiteOAuthCredentialVault(
            self.path, cipher=self.cipher, hmac_key=b"h" * 32, clock=lambda: 1000
        )

    def store(self, owner="owner-a", account="acct-1"):
        return self.vault.store(
            owner_id=owner, provider_id="upwork", account_id=account,
            access_token="access.token-1", refresh_token="refresh.token-1",
            expires_at=2000, scopes=["profile:read", "jobs:read"],
        )

    def test_persists_encrypted_tokens_and_round_trips_after_restart(self):
        self.store()
        raw = Path(self.path).read_bytes()
        self.assertNotIn(b"access.token-1", raw)
        self.assertNotIn(b"refresh.token-1", raw)
        restarted = SQLiteOAuthCredentialVault(
            self.path, cipher=self.cipher, hmac_key=b"h" * 32, clock=lambda: 1000
        )
        record = restarted.get(owner_id="owner-a", provider_id="upwork", account_id="acct-1")
        self.assertEqual(record.access_token, "access.token-1")
        self.assertEqual(record.refresh_token, "refresh.token-1")
        self.assertEqual(record.version, 1)
        self.assertNotIn("access.token-1", repr(record))

    def test_owner_and_account_isolation(self):
        self.store()
        self.assertIsNone(self.vault.get(owner_id="owner-b", provider_id="upwork", account_id="acct-1"))
        self.assertIsNone(self.vault.get(owner_id="owner-a", provider_id="upwork", account_id="other"))
        self.vault.store(owner_id="owner-b", provider_id="upwork", account_id="acct-1",
                         access_token="other.access", refresh_token=None, expires_at=2000, scopes=[])
        self.assertEqual(len(self.vault.list_accounts(owner_id="owner-a")), 1)
        self.assertEqual(len(self.vault.list_accounts(owner_id="owner-b")), 1)

    def test_duplicate_store_rejected(self):
        self.store()
        with self.assertRaisesRegex(OAuthCredentialError, "credential_already_exists"):
            self.store()

    def test_refresh_rotation_is_compare_and_swap_and_one_time(self):
        self.store()
        version = self.vault.rotate(
            owner_id="owner-a", provider_id="upwork", account_id="acct-1",
            expected_version=1, expected_refresh_token="refresh.token-1",
            access_token="access.token-2", refresh_token="refresh.token-2",
            expires_at=3000, scopes=["jobs:read"],
        )
        self.assertEqual(version, 2)
        record = self.vault.get(owner_id="owner-a", provider_id="upwork", account_id="acct-1")
        self.assertEqual((record.access_token, record.refresh_token, record.scopes),
                         ("access.token-2", "refresh.token-2", ("jobs:read",)))
        with self.assertRaisesRegex(OAuthCredentialError, "stale_refresh_generation"):
            self.vault.rotate(
                owner_id="owner-a", provider_id="upwork", account_id="acct-1",
                expected_version=1, expected_refresh_token="refresh.token-1",
                access_token="access.token-3", refresh_token="refresh.token-3",
                expires_at=3000, scopes=["jobs:read"],
            )

    def test_refresh_token_is_retained_when_rotation_response_omits_it(self):
        self.store()
        self.vault.rotate(
            owner_id="owner-a", provider_id="upwork", account_id="acct-1",
            expected_version=1, expected_refresh_token="refresh.token-1",
            access_token="access.token-2", refresh_token=None,
            expires_at=3000, scopes=["jobs:read"],
        )
        record = self.vault.get(owner_id="owner-a", provider_id="upwork", account_id="acct-1")
        self.assertEqual(record.refresh_token, "refresh.token-1")
        self.vault.rotate(
            owner_id="owner-a", provider_id="upwork", account_id="acct-1",
            expected_version=2, expected_refresh_token="refresh.token-1",
            access_token="access.token-3", refresh_token="refresh.token-3",
            expires_at=4000, scopes=["jobs:read"],
        )

    def test_refresh_rotation_rejects_wrong_token(self):
        self.store()
        with self.assertRaisesRegex(OAuthCredentialError, "stale_refresh_generation"):
            self.vault.rotate(
                owner_id="owner-a", provider_id="upwork", account_id="acct-1",
                expected_version=1, expected_refresh_token="wrong.refresh",
                access_token="access.token-2", refresh_token="refresh.token-2",
                expires_at=3000, scopes=[],
            )

    def test_aead_associated_data_prevents_cross_account_ciphertext_swap(self):
        self.store(account="acct-1")
        self.vault.store(owner_id="owner-a", provider_id="upwork", account_id="acct-2",
                         access_token="second.access", refresh_token=None, expires_at=2000, scopes=[])
        with self.vault._connect() as db:
            first = db.execute("SELECT access_ciphertext FROM oauth_credentials WHERE account_id='acct-1'").fetchone()[0]
            db.execute("UPDATE oauth_credentials SET access_ciphertext=? WHERE account_id='acct-2'", (first,))
        with self.assertRaisesRegex(OAuthCredentialError, "access_token_decryption_failed"):
            self.vault.get(owner_id="owner-a", provider_id="upwork", account_id="acct-2")

    def test_disconnect_deletes_credentials_and_is_idempotent(self):
        self.store()
        self.assertTrue(self.vault.disconnect(owner_id="owner-a", provider_id="upwork", account_id="acct-1"))
        self.assertFalse(self.vault.disconnect(owner_id="owner-a", provider_id="upwork", account_id="acct-1"))
        self.assertIsNone(self.vault.get(owner_id="owner-a", provider_id="upwork", account_id="acct-1"))

    def test_version_fenced_disconnect_cannot_delete_a_newer_credential(self):
        self.store()
        self.assertFalse(self.vault.disconnect(
            owner_id="owner-a", provider_id="upwork", account_id="acct-1",
            expected_version=2,
        ))
        self.assertEqual(self.vault.get(
            owner_id="owner-a", provider_id="upwork", account_id="acct-1"
        ).version, 1)
        self.assertTrue(self.vault.disconnect(
            owner_id="owner-a", provider_id="upwork", account_id="acct-1",
            expected_version=1,
        ))

    def test_disconnect_service_integrates_with_real_vault_and_revokes_once(self):
        from core.oauth_disconnect_service import OAuthDisconnectService

        class Transport:
            def __init__(self):
                self.calls = []
            def request_form_status(self, endpoint, *, fields):
                self.calls.append((endpoint, fields))
                return 200

        self.store()
        transport = Transport()
        service = OAuthDisconnectService(
            credential_vault=self.vault, transport=transport,
            provider_id="upwork", client_id="client",
            revocation_endpoint="https://auth.example.com/revoke",
        )
        result = service.disconnect(owner_id="owner-a", account_id="acct-1")
        self.assertTrue(result["disconnected"])
        self.assertTrue(result["remote_revoked"])
        self.assertEqual(len(transport.calls), 1)
        self.assertEqual(transport.calls[0][1]["token"], "refresh.token-1")
        self.assertIsNone(self.vault.get(
            owner_id="owner-a", provider_id="upwork", account_id="acct-1"
        ))
        self.assertNotIn("refresh.token-1", repr(result))

    def test_stale_disconnect_does_not_revoke_or_delete_new_vault_generation(self):
        from core.oauth_disconnect_service import OAuthDisconnectService

        class RefreshingVault(SQLiteOAuthCredentialVault):
            def get(self, **kwargs):
                stale = super().get(**kwargs)
                if stale is not None and not getattr(self, "_rotated", False):
                    self._rotated = True
                    super().rotate(
                        owner_id=kwargs["owner_id"], provider_id=kwargs["provider_id"],
                        account_id=kwargs["account_id"], expected_version=stale.version,
                        expected_refresh_token=stale.refresh_token,
                        access_token="access.token-2", refresh_token="refresh.token-2",
                        expires_at=3000, scopes=stale.scopes,
                    )
                return stale

        vault = RefreshingVault(
            self.path, cipher=self.cipher, hmac_key=b"h" * 32, clock=lambda: 1000
        )
        vault.store(
            owner_id="owner-a", provider_id="upwork", account_id="acct-1",
            access_token="access.token-1", refresh_token="refresh.token-1",
            expires_at=2000, scopes=["profile:read"],
        )
        class Transport:
            def __init__(self): self.calls = []
            def request_form_status(self, endpoint, *, fields):
                self.calls.append(fields)
                return 200
        transport = Transport()
        service = OAuthDisconnectService(
            credential_vault=vault, transport=transport,
            provider_id="upwork", client_id="client",
            revocation_endpoint="https://auth.example.com/revoke",
        )
        result = service.disconnect(owner_id="owner-a", account_id="acct-1")
        self.assertFalse(result["disconnected"])
        self.assertFalse(result["revocation_attempted"])
        self.assertEqual(transport.calls, [])
        current = vault.get(owner_id="owner-a", provider_id="upwork", account_id="acct-1")
        self.assertEqual((current.version, current.refresh_token), (2, "refresh.token-2"))

    def test_rejects_expired_tokens_and_invalid_scopes(self):
        with self.assertRaisesRegex(OAuthCredentialError, "invalid_token_expiry"):
            self.vault.store(owner_id="owner-a", provider_id="upwork", account_id="acct-1",
                             access_token="access.token", refresh_token=None, expires_at=1000, scopes=[])
        with self.assertRaisesRegex(OAuthCredentialError, "invalid_scopes"):
            self.vault.store(owner_id="owner-a", provider_id="upwork", account_id="acct-1",
                             access_token="access.token", refresh_token=None, expires_at=2000, scopes=["bad scope"])

    def test_corrupt_or_tampered_ciphertext_fails_closed(self):
        self.store()
        with self.vault._connect() as db:
            db.execute("UPDATE oauth_credentials SET access_ciphertext=? WHERE account_id='acct-1'", (b"tampered",))
        with self.assertRaisesRegex(OAuthCredentialError, "access_token_decryption_failed"):
            self.vault.get(owner_id="owner-a", provider_id="upwork", account_id="acct-1")


if __name__ == "__main__":
    unittest.main()
