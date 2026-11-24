"""Durable SQLite replay and revocation state for audit-anchor credentials."""
from __future__ import annotations

from contextlib import contextmanager
import math
import sqlite3
import time
from typing import Callable

from core.sqlite_file_security import prepare_private_sqlite_path


class SQLiteAuditAnchorCredentialState:
    """Atomic one-use and revocation state for anchor publisher credentials.

    Use one database on one host/local filesystem for all authenticator workers
    sharing this state. SQLite locking coordinates processes on that host; this
    class is not a multi-region or network-filesystem coordination service.
    """

    def __init__(
        self,
        database_path: str,
        *,
        timeout_seconds: float = 5.0,
        clock: Callable[[], float] = time.time,
    ):
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(timeout_seconds)
            or not 0 < timeout_seconds <= 30
        ):
            raise ValueError("credential_state_timeout_invalid")
        if not callable(clock):
            raise TypeError("credential_state_clock_invalid")
        self.database_path = prepare_private_sqlite_path(database_path)
        self.timeout_seconds = float(timeout_seconds)
        self._clock = clock
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS consumed_credentials (
                    issuer TEXT NOT NULL,
                    token_id TEXT NOT NULL,
                    expires_at INTEGER NOT NULL CHECK(expires_at >= 0),
                    consumed_at INTEGER NOT NULL CHECK(consumed_at >= 0),
                    PRIMARY KEY (issuer, token_id)
                );
                CREATE INDEX IF NOT EXISTS consumed_credentials_expiry
                    ON consumed_credentials(expires_at);
                CREATE TABLE IF NOT EXISTS revoked_credentials (
                    issuer TEXT NOT NULL,
                    token_id TEXT NOT NULL,
                    revoked_at INTEGER NOT NULL CHECK(revoked_at >= 0),
                    PRIMARY KEY (issuer, token_id)
                );
                """
            )

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(
            self.database_path, timeout=self.timeout_seconds, isolation_level=None
        )
        try:
            db.execute(f"PRAGMA busy_timeout = {int(self.timeout_seconds * 1000)}")
            yield db
        finally:
            db.close()

    @staticmethod
    def _validate_identity(issuer: object, token_id: object) -> None:
        for value, label in ((issuer, "issuer"), (token_id, "token_id")):
            if (
                not isinstance(value, str) or not value or len(value) > 256
                or value != value.strip()
                or any(ord(char) < 33 or ord(char) > 126 for char in value)
            ):
                raise ValueError("credential_" + label + "_invalid")

    def _now(self) -> int:
        value = self._clock()
        if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
            raise ValueError("credential_state_clock_invalid")
        return int(value)

    def is_revoked(self, issuer: str, token_id: str) -> bool:
        self._validate_identity(issuer, token_id)
        with self._connect() as db:
            row = db.execute(
                "SELECT 1 FROM revoked_credentials WHERE issuer = ? AND token_id = ?",
                (issuer, token_id),
            ).fetchone()
        return row is not None

    def consume_once(self, issuer: str, token_id: str, expires_at: int) -> bool:
        self._validate_identity(issuer, token_id)
        if type(expires_at) is not int or expires_at < 0:
            raise ValueError("credential_expiry_invalid")
        now = self._now()
        if expires_at <= now:
            return False
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "DELETE FROM consumed_credentials WHERE expires_at <= ?", (now,)
            )
            revoked = db.execute(
                "SELECT 1 FROM revoked_credentials WHERE issuer = ? AND token_id = ?",
                (issuer, token_id),
            ).fetchone()
            if revoked is not None:
                db.rollback()
                return False
            try:
                db.execute(
                    """
                    INSERT INTO consumed_credentials(issuer, token_id, expires_at, consumed_at)
                    VALUES(?, ?, ?, ?)
                    """,
                    (issuer, token_id, expires_at, now),
                )
            except sqlite3.IntegrityError:
                db.rollback()
                return False
            db.commit()
        return True

    def revoke(self, issuer: str, token_id: str) -> bool:
        """Permanently revoke this issuer/token ID; idempotent on repeat calls."""
        self._validate_identity(issuer, token_id)
        now = self._now()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            cursor = db.execute(
                """
                INSERT OR IGNORE INTO revoked_credentials(issuer, token_id, revoked_at)
                VALUES(?, ?, ?)
                """,
                (issuer, token_id, now),
            )
            db.commit()
        return cursor.rowcount == 1
