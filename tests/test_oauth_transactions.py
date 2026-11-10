import base64
import hashlib
import sqlite3
import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from core.oauth_transactions import OAuthPKCETransactionStore, OAuthTransactionError


class _TestVault:
    def __init__(self):
        self.key = b"test-only-vault-key"

    def encrypt(self, plaintext, associated_data):
        mask = hashlib.sha256(self.key + associated_data).digest()
        return bytes(value ^ mask[i % len(mask)] for i, value in enumerate(plaintext))

    def decrypt(self, ciphertext, associated_data):
        return self.encrypt(ciphertext, associated_data)


class OAuthPKCETransactionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.temp.name) / "oauth.sqlite3")
        self.vault = _TestVault()
        self.store = OAuthPKCETransactionStore(
            self.path, encrypt=self.vault.encrypt, decrypt=self.vault.decrypt,
            allowed_authorization_hosts=["www.upwork.com"],
        )

    def tearDown(self):
        self.temp.cleanup()

    def _create(self, **overrides):
        values = {
            "owner_id": "owner-1", "provider_id": "upwork",
            "issuer": "https://www.upwork.com",
            "authorization_endpoint": "https://www.upwork.com/oauth/v2/authorize",
            "client_id": "registered-client",
            "redirect_uri": "https://yuki.example.com/oauth/callback",
            "scopes": ["jobs:read", "profile:read"],
        }
        values.update(overrides)
        return self.store.create(**values)

    def test_generates_state_s256_and_registered_redirect_parameters(self):
        result = self._create()
        query = parse_qs(urlsplit(result["authorization_url"]).query)
        self.assertEqual(query["state"], [result["state"]])
        self.assertEqual(query["code_challenge_method"], ["S256"])
        verifier = self.store.consume(state=result["state"], owner_id="owner-1",
                                      provider_id="upwork", issuer="https://www.upwork.com")
        expected = base64.urlsafe_b64encode(
            hashlib.sha256(verifier["code_verifier"].encode("ascii")).digest()
        ).rstrip(b"=").decode("ascii")
        self.assertEqual(query["code_challenge"], [expected])
        self.assertEqual(verifier["client_id"], "registered-client")
        self.assertEqual(verifier["scopes"], ["jobs:read", "profile:read"])

    def test_database_never_contains_plaintext_state_or_verifier_or_scopes(self):
        result = self._create()
        with open(self.path, "rb") as stream:
            raw = stream.read()
        self.assertNotIn(result["state"].encode(), raw)
        self.assertNotIn(b"code_verifier", raw)

    def test_transaction_is_single_use(self):
        result = self._create()
        self.store.consume(state=result["state"], owner_id="owner-1",
                           provider_id="upwork", issuer="https://www.upwork.com")
        with self.assertRaises(OAuthTransactionError):
            self.store.consume(state=result["state"], owner_id="owner-1",
                               provider_id="upwork", issuer="https://www.upwork.com")

    def test_owner_provider_and_issuer_are_bound(self):
        result = self._create()
        for kwargs in (
            {"owner_id": "other", "provider_id": "upwork", "issuer": "https://www.upwork.com"},
            {"owner_id": "owner-1", "provider_id": "other", "issuer": "https://www.upwork.com"},
            {"owner_id": "owner-1", "provider_id": "upwork", "issuer": "https://attacker.example"},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(OAuthTransactionError):
                self.store.consume(state=result["state"], **kwargs)
        with self.assertRaises(OAuthTransactionError):
            self.store.consume(state=result["state"], owner_id="owner-1",
                               provider_id="upwork", issuer="https://www.upwork.com")

    def test_expired_transaction_is_rejected(self):
        result = self._create()
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE oauth_pkce_transactions SET expires_at='2000-01-01T00:00:00+00:00'")
        with self.assertRaises(OAuthTransactionError):
            self.store.consume(state=result["state"], owner_id="owner-1",
                               provider_id="upwork", issuer="https://www.upwork.com")

    def test_invalid_urls_scopes_and_vault_are_rejected(self):
        with self.assertRaises(ValueError):
            self._create(issuer="http://upwork.com")
        with self.assertRaises(ValueError):
            self._create(scopes=["bad scope"])
        with self.assertRaises(ValueError):
            self._create(authorization_endpoint="https://attacker.example/authorize")
        broken = OAuthPKCETransactionStore(
            str(Path(self.temp.name) / "broken.sqlite3"),
            encrypt=lambda *_: (_ for _ in ()).throw(RuntimeError("secret")),
            decrypt=self.vault.decrypt,
            allowed_authorization_hosts=["www.upwork.com"],
        )
        with self.assertRaises(OAuthTransactionError):
            broken.create(owner_id="owner-1", provider_id="upwork", issuer="https://upwork.com",
                          authorization_endpoint="https://www.upwork.com/auth", client_id="id",
                          redirect_uri="https://yuki.example.com/callback", scopes=["jobs:read"])

    def test_reserved_endpoint_parameters_cannot_override_security_values(self):
        with self.assertRaises(ValueError):
            self._create(authorization_endpoint="https://www.upwork.com/auth?state=attacker")

    def test_pending_transaction_capacity_is_bounded(self):
        self.store.MAX_PENDING_PER_OWNER = 1
        self._create()
        with self.assertRaises(OAuthTransactionError):
            self._create()

    def test_state_must_be_canonical_base64url(self):
        result = self._create()
        with self.assertRaises(OAuthTransactionError):
            self.store.consume(state="!" + result["state"][1:], owner_id="owner-1",
                               provider_id="upwork", issuer="https://www.upwork.com")


if __name__ == "__main__":
    unittest.main()
