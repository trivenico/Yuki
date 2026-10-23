import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from core.auth import BuiltInAuthService


class AuthSessionRetentionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.auth = BuiltInAuthService(
            str(Path(self.tmp.name) / "auth.sqlite3"),
            password_iterations=100_000,
        )
        self.auth.bootstrap_admin("root.user", "A secure local password 7!")

    def test_cleanup_removes_old_expired_and_revoked_sessions_only(self):
        expired = self.auth.authenticate("root.user", "A secure local password 7!")
        revoked = self.auth.authenticate("root.user", "A secure local password 7!")
        recent = self.auth.authenticate("root.user", "A secure local password 7!")
        self.auth.revoke_session(revoked.token)
        old = (datetime.now(timezone.utc) - timedelta(days=45)).isoformat()
        with self.auth._connect() as db:
            db.execute(
                "UPDATE auth_sessions SET expires_at=?, created_at=? WHERE token_hash=?",
                (old, old, __import__("hashlib").sha256(expired.token.encode("ascii")).hexdigest()),
            )
            db.execute(
                "UPDATE auth_sessions SET revoked_at=? WHERE token_hash=?",
                (old, __import__("hashlib").sha256(revoked.token.encode("ascii")).hexdigest()),
            )
        removed = self.auth.cleanup_sessions(retention_days=30)
        self.assertEqual(removed, 2)
        self.assertIsNone(self.auth.resolve_session(expired.token))
        self.assertIsNone(self.auth.resolve_session(revoked.token))
        self.assertIsNotNone(self.auth.resolve_session(recent.token))

    def test_cleanup_retention_range_is_validated(self):
        for value in (0, 366, True, 1.5):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    self.auth.cleanup_sessions(retention_days=value)

    def test_expired_session_is_invalid_even_before_cleanup(self):
        session = self.auth.authenticate("root.user", "A secure local password 7!")
        past = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat()
        with self.auth._connect() as db:
            db.execute(
                "UPDATE auth_sessions SET expires_at=? WHERE token_hash=?",
                (past, __import__("hashlib").sha256(session.token.encode("ascii")).hexdigest()),
            )
        self.assertIsNone(self.auth.resolve_session(session.token))


if __name__ == "__main__":
    unittest.main()
