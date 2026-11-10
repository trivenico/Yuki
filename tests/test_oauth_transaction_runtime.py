import tempfile
import unittest
from pathlib import Path

from core.oauth_os_key_provider import OSKeyringOAuthKeyProvider, OAuthOSKeyStoreError
from core.oauth_transaction_runtime import build_oauth_transaction_store


class MemoryCredentialBackend:
    def __init__(self):
        self.values = {}

    def get_password(self, service, username):
        return self.values.get((service, username))

    def set_password(self, service, username, password):
        self.values[(service, username)] = password


class OAuthTransactionRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.temp.name) / "oauth-transactions.sqlite3")
        self.backend = MemoryCredentialBackend()
        self.provider = OSKeyringOAuthKeyProvider(service_name="transaction-test", backend=self.backend)

    def tearDown(self):
        self.temp.cleanup()

    def test_store_fails_closed_until_explicit_os_key_initialization(self):
        with self.assertRaisesRegex(OAuthOSKeyStoreError, "not_initialized"):
            build_oauth_transaction_store(self.path, key_provider=self.provider)

    def test_transaction_survives_restart_and_is_consumed_once(self):
        self.provider.initialize()
        store = build_oauth_transaction_store(self.path, key_provider=self.provider)
        store.create(
            state="state-value-123456789012345678901234",
            code_verifier="verifier-value-123456789012345678901234567890123456789012345678",
            owner_id="owner-1", session_id="session-1",
            redirect_uri="https://yuki.example/oauth/callback",
            issuer="https://mcp.example", resource="https://mcp.example/mcp",
        )
        restarted = build_oauth_transaction_store(
            self.path,
            key_provider=OSKeyringOAuthKeyProvider(service_name="transaction-test", backend=self.backend),
        )
        result = restarted.consume(
            state="state-value-123456789012345678901234",
            owner_id="owner-1", session_id="session-1",
        )
        self.assertEqual(result.code_verifier, "verifier-value-123456789012345678901234567890123456789012345678")
        with self.assertRaisesRegex(ValueError, "already_consumed"):
            restarted.consume(
                state="state-value-123456789012345678901234",
                owner_id="owner-1", session_id="session-1",
            )

    def test_wrong_owner_or_session_cannot_consume_transaction(self):
        self.provider.initialize()
        store = build_oauth_transaction_store(self.path, key_provider=self.provider)
        store.create(
            state="state-value-123456789012345678901234",
            code_verifier="verifier-value-123456789012345678901234567890123456789012345678",
            owner_id="owner-1", session_id="session-1",
            redirect_uri="https://yuki.example/oauth/callback",
            issuer="https://mcp.example", resource="https://mcp.example/mcp",
        )
        with self.assertRaisesRegex(ValueError, "session_mismatch"):
            store.consume(
                state="state-value-123456789012345678901234",
                owner_id="owner-1", session_id="session-2",
            )
        result = store.consume(
            state="state-value-123456789012345678901234",
            owner_id="owner-1", session_id="session-1",
        )
        self.assertTrue(result.code_verifier.startswith("verifier-"))

    def test_ciphertext_cannot_be_transplanted_between_transactions(self):
        self.provider.initialize()
        store = build_oauth_transaction_store(self.path, key_provider=self.provider)
        first = "state-value-123456789012345678901234"
        second = "state-value-223456789012345678901234"
        verifier = "verifier-value-123456789012345678901234567890123456789012345678"
        for state in (first, second):
            store.create(
                state=state, code_verifier=verifier,
                owner_id="owner-1", session_id="session-1",
                redirect_uri="https://yuki.example/oauth/callback",
                issuer="https://mcp.example", resource="https://mcp.example/mcp",
            )
        import sqlite3
        db = sqlite3.connect(self.path)
        try:
            rows = db.execute(
                "SELECT state_digest, verifier_ciphertext FROM oauth_transactions ORDER BY created_at, state_digest"
            ).fetchall()
            db.execute(
                "UPDATE oauth_transactions SET verifier_ciphertext=? WHERE state_digest=?",
                (rows[0][1], rows[1][0]),
            )
            db.commit()
        finally:
            db.close()
        with self.assertRaisesRegex(ValueError, "verifier_decryption_failed"):
            store.consume(state=second, owner_id="owner-1", session_id="session-1")


if __name__ == "__main__":
    unittest.main()
