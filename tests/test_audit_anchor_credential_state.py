import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from core.audit_anchor_credential_state import SQLiteAuditAnchorCredentialState


class SQLiteAuditAnchorCredentialStateTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = str(Path(self.directory.name) / "credential-state.sqlite3")
        self.now = 1_800_000_000
        self.state = SQLiteAuditAnchorCredentialState(
            self.path, clock=lambda: self.now,
        )

    def tearDown(self):
        self.directory.cleanup()

    def test_consumes_once_and_persists_across_instances(self):
        self.assertTrue(self.state.consume_once("anchor-issuer", "token-1", self.now + 60))
        reopened = SQLiteAuditAnchorCredentialState(self.path, clock=lambda: self.now)
        self.assertFalse(reopened.consume_once("anchor-issuer", "token-1", self.now + 60))

    def test_concurrent_consumption_has_exactly_one_winner(self):
        def consume(_):
            instance = SQLiteAuditAnchorCredentialState(
                self.path, clock=lambda: self.now,
            )
            return instance.consume_once("anchor-issuer", "one-race", self.now + 60)
        with ThreadPoolExecutor(max_workers=8) as pool:
            outcomes = list(pool.map(consume, range(16)))
        self.assertEqual(sum(outcomes), 1)

    def test_revocation_blocks_unconsumed_credential_and_is_durable(self):
        self.assertTrue(self.state.revoke("anchor-issuer", "token-revoked"))
        self.assertFalse(self.state.revoke("anchor-issuer", "token-revoked"))
        reopened = SQLiteAuditAnchorCredentialState(self.path, clock=lambda: self.now)
        self.assertTrue(reopened.is_revoked("anchor-issuer", "token-revoked"))
        self.assertFalse(reopened.consume_once(
            "anchor-issuer", "token-revoked", self.now + 60,
        ))

    def test_expired_credentials_cannot_be_consumed(self):
        self.assertFalse(self.state.consume_once(
            "anchor-issuer", "already-expired", self.now,
        ))

    def test_expired_consumption_records_are_pruned(self):
        self.assertTrue(self.state.consume_once(
            "anchor-issuer", "expired-record", self.now + 1,
        ))
        self.now += 1
        self.assertTrue(self.state.consume_once(
            "anchor-issuer", "new-token", self.now + 60,
        ))
        import sqlite3
        with sqlite3.connect(self.path) as db:
            count = db.execute(
                "SELECT COUNT(*) FROM consumed_credentials WHERE token_id='expired-record'"
            ).fetchone()[0]
        self.assertEqual(count, 0)

    def test_private_file_permissions(self):
        if os.name == "posix":
            self.assertEqual(Path(self.path).stat().st_mode & 0o777, 0o600)

    def test_rejects_boolean_expiry(self):
        with self.assertRaisesRegex(ValueError, "expiry_invalid"):
            self.state.consume_once("anchor-issuer", "token-bool", True)


if __name__ == "__main__":
    unittest.main()
