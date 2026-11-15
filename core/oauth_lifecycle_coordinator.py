"""Process-safe coordination for OAuth credential lifecycle operations.

All workers that share OAuth credentials must use the same coordinator database
on a local filesystem. A dedicated SQLite file is used rather than the credential
vault database so a lifecycle lock can remain held during a provider request
without blocking the vault's own writes.

SQLite releases the transaction lock when a process exits, including after a
crash. This implementation deliberately serializes lifecycle operations
globally: SQLite has one writer at a time. It favors correctness over parallel
throughput until a database with keyed advisory locks is introduced.
"""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import re
import sqlite3
from typing import Iterator

from core.sqlite_file_security import prepare_private_sqlite_path


_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@+-]{0,255}$")


class OAuthLifecycleCoordinationError(RuntimeError):
    """Stable, non-sensitive failure to acquire lifecycle coordination."""


class SQLiteOAuthLifecycleCoordinator:
    """Serialize OAuth lifecycle critical sections across local worker processes.

    The database path is deployment configuration and must be identical for all
    API and worker processes. Do not put this database on NFS or another
    filesystem without reliable SQLite locking semantics. Do not nest
    coordinate() calls using the same database.
    """

    def __init__(self, database_path: str, *, busy_timeout_ms: int = 5000):
        if not isinstance(database_path, str) or not database_path.strip():
            raise ValueError("database_path must be non-empty")
        if type(busy_timeout_ms) is not int or not 1 <= busy_timeout_ms <= 30000:
            raise ValueError("busy_timeout_ms must be between 1 and 30000")
        self.database_path = prepare_private_sqlite_path(database_path)
        self._busy_timeout_ms = busy_timeout_ms
        try:
            with self._connect() as db:
                db.execute("PRAGMA journal_mode=WAL")
                db.execute("CREATE TABLE IF NOT EXISTS oauth_lifecycle_lock (singleton INTEGER PRIMARY KEY CHECK(singleton = 1))")
                db.execute("INSERT OR IGNORE INTO oauth_lifecycle_lock(singleton) VALUES(1)")
        except sqlite3.Error:
            raise OAuthLifecycleCoordinationError("coordinator_initialization_failed") from None

    def _connect(self):
        db = sqlite3.connect(
            self.database_path,
            timeout=self._busy_timeout_ms / 1000,
            isolation_level=None,
        )
        db.execute(f"PRAGMA busy_timeout={self._busy_timeout_ms}")
        return db

    @staticmethod
    def _validate_identity(owner_id: str, provider_id: str, account_id: str | None) -> None:
        if not isinstance(owner_id, str) or not _IDENTIFIER.fullmatch(owner_id):
            raise ValueError("owner_id is invalid")
        if not isinstance(provider_id, str) or not _IDENTIFIER.fullmatch(provider_id):
            raise ValueError("provider_id is invalid")
        if account_id is not None and (
            not isinstance(account_id, str) or not _IDENTIFIER.fullmatch(account_id)
        ):
            raise ValueError("account_id is invalid")

    @contextmanager
    def coordinate(
        self, *, owner_id: str, provider_id: str, account_id: str | None = None,
    ) -> Iterator[None]:
        """Hold a process-wide SQLite write lock for one lifecycle operation.

        Identity is validated but intentionally not persisted: the current
        SQLite implementation uses a global lock and stores no account metadata.
        """
        self._validate_identity(owner_id, provider_id, account_id)
        db = None
        try:
            db = self._connect()
            db.execute("BEGIN IMMEDIATE")
            yield
            db.execute("COMMIT")
        except sqlite3.Error:
            if db is not None and db.in_transaction:
                db.execute("ROLLBACK")
            raise OAuthLifecycleCoordinationError("coordinator_lock_unavailable") from None
        except BaseException:
            if db is not None and db.in_transaction:
                db.execute("ROLLBACK")
            raise
        finally:
            if db is not None:
                db.close()
