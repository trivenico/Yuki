import sqlite3
from contextlib import closing
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from core.oauth_transactions import OAuthTransactionError, SQLiteOAuthTransactionStore


class OAuthTransactionStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.temp.name) / "oauth.sqlite3")
        self.now = [1_800_000_000]
        self.store = self._store()

    def tearDown(self):
        self.temp.cleanup()

    def _store(self, **overrides):
        options = {
            "database_path": self.path,
            "hmac_key": b"h" * 32,
            "encrypt": lambda value: b"cipher:" + value[::-1],
            "decrypt": lambda value: value[len(b"cipher:"):][::-1],
            "clock": lambda: self.now[0],
            "max_ttl_seconds": 900,
        }
        options.update(overrides)
        return SQLiteOAuthTransactionStore(**options)

    def _create(self, state="state-random-123", **overrides):
        values = {
            "state": state,
            "code_verifier": "pkce-verifier-secret",
            "owner_id": "owner-123",
            "session_id": "session-456",
            "redirect_uri": "https://yuki.example/callback",
            "issuer": "https://login.example",
            "resource": "https://mcp.example/mcp",
            "ttl_seconds": 600,
        }
        values.update(overrides)
        return self.store.create(**values)

    def test_round_trip_and_persistence_across_restart(self):
        expires = self._create()
        restarted = self._store()
        result = restarted.consume(
            state="state-random-123", owner_id="owner-123", session_id="session-456"
        )
        self.assertEqual(result.code_verifier, "pkce-verifier-secret")
        self.assertEqual(result.redirect_uri, "https://yuki.example/callback")
        self.assertEqual(result.issuer, "https://login.example")
        self.assertEqual(result.resource, "https://mcp.example/mcp")
        self.assertEqual(result.expires_at, expires)

    def test_sensitive_values_are_not_stored_in_plaintext(self):
        self._create()
        with closing(sqlite3.connect(self.path)) as db:
            row = db.execute(
                "SELECT state_digest, owner_digest, session_digest, verifier_ciphertext "
                "FROM oauth_transactions"
            ).fetchone()
        for raw in (b"state-random-123", b"owner-123", b"session-456", b"pkce-verifier-secret"):
            self.assertNotIn(raw, [str(row[0]).encode(), str(row[1]).encode(),
                                   str(row[2]).encode(), bytes(row[3])])

    def test_owner_or_session_mismatch_does_not_consume_transaction(self):
        self._create()
        for kwargs, error in (
            ({"owner_id": "other", "session_id": "session-456"}, "transaction_owner_mismatch"),
            ({"owner_id": "owner-123", "session_id": "other"}, "transaction_session_mismatch"),
        ):
            with self.subTest(kwargs=kwargs), self.assertRaisesRegex(OAuthTransactionError, error):
                self.store.consume(state="state-random-123", **kwargs)
        result = self.store.consume(
            state="state-random-123", owner_id="owner-123", session_id="session-456"
        )
        self.assertEqual(result.code_verifier, "pkce-verifier-secret")

    def test_state_can_only_be_consumed_once(self):
        self._create()
        args = dict(state="state-random-123", owner_id="owner-123", session_id="session-456")
        self.store.consume(**args)
        with self.assertRaisesRegex(OAuthTransactionError, "transaction_already_consumed"):
            self.store.consume(**args)

    def test_concurrent_consumers_have_exactly_one_winner(self):
        self._create()
        barrier = threading.Barrier(2)
        def consume():
            barrier.wait()
            try:
                self.store.consume(
                    state="state-random-123", owner_id="owner-123", session_id="session-456"
                )
                return "success"
            except OAuthTransactionError as exc:
                return str(exc)
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(lambda _: consume(), range(2)))
        self.assertEqual(outcomes.count("success"), 1)
        self.assertEqual(outcomes.count("transaction_already_consumed"), 1)

    def test_expired_transaction_is_rejected_and_consumed(self):
        self._create(ttl_seconds=60)
        self.now[0] += 60
        args = dict(state="state-random-123", owner_id="owner-123", session_id="session-456")
        with self.assertRaisesRegex(OAuthTransactionError, "transaction_expired"):
            self.store.consume(**args)
        with self.assertRaisesRegex(OAuthTransactionError, "transaction_already_consumed"):
            self.store.consume(**args)

    def test_duplicate_state_and_ttl_bounds_are_rejected(self):
        self._create()
        with self.assertRaisesRegex(OAuthTransactionError, "duplicate_transaction_state"):
            self._create()
        for ttl in (0, 59, 901, True):
            with self.subTest(ttl=ttl), self.assertRaises(OAuthTransactionError):
                self._create(state="state-" + str(ttl), ttl_seconds=ttl)

    def test_encryption_failure_does_not_create_transaction(self):
        broken = self._store(encrypt=lambda value: (_ for _ in ()).throw(RuntimeError()))
        with self.assertRaisesRegex(OAuthTransactionError, "verifier_encryption_failed"):
            broken.create(
                state="another-state", code_verifier="verifier", owner_id="owner",
                session_id="session", redirect_uri="https://yuki.example/callback",
                issuer="https://login.example", resource="https://mcp.example/mcp",
            )
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM oauth_transactions").fetchone()[0], 0)

    def test_decryption_failure_consumes_transaction_fail_closed(self):
        self._create()
        broken = self._store(decrypt=lambda value: (_ for _ in ()).throw(RuntimeError()))
        args = dict(state="state-random-123", owner_id="owner-123", session_id="session-456")
        with self.assertRaisesRegex(OAuthTransactionError, "verifier_decryption_failed"):
            broken.consume(**args)
        with self.assertRaisesRegex(OAuthTransactionError, "transaction_already_consumed"):
            self.store.consume(**args)

    def test_purge_expired_retains_recent_transactions(self):
        self._create()
        self._create(state="second-state")
        self.now[0] += 600
        self.assertEqual(self.store.purge_expired(retention_seconds=0), 2)
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM oauth_transactions").fetchone()[0], 0)

    def test_requires_separate_strong_hmac_key_and_cipher(self):
        with self.assertRaises(ValueError):
            self._store(hmac_key=b"short")
        with self.assertRaises(ValueError):
            self._store(encrypt=None)
        with self.assertRaises(ValueError):
            self._store(max_ttl_seconds=3600)


if __name__ == "__main__":
    unittest.main()
