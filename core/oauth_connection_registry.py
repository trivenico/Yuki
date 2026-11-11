"""SQLite registry of non-secret owner-scoped OAuth connection metadata."""
from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
import time
from dataclasses import dataclass

_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@+-]{0,255}$")
_SCOPE = re.compile(r"^[\x21\x23-\x5B\x5D-\x7E]{1,128}$")


@dataclass(frozen=True)
class OAuthConnectionRecord:
    provider_id: str
    account_id: str
    scopes: tuple[str, ...]
    connected_at: int


class SQLiteOAuthConnectionRegistry:
    """Persist connection metadata only; bearer and refresh tokens never enter here."""

    def __init__(self, database_path: str, *, clock=time.time):
        if not isinstance(database_path, str) or not database_path.strip():
            raise ValueError("database_path must be non-empty")
        if not callable(clock):
            raise ValueError("clock must be callable")
        self.database_path = str(Path(database_path).expanduser())
        Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
        self._clock = clock
        with self._connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("""
                CREATE TABLE IF NOT EXISTS oauth_connections (
                    owner_id TEXT NOT NULL,
                    provider_id TEXT NOT NULL,
                    account_id TEXT NOT NULL,
                    scopes_json TEXT NOT NULL,
                    connected_at INTEGER NOT NULL,
                    PRIMARY KEY(owner_id, provider_id, account_id)
                )
            """)
            db.execute("CREATE INDEX IF NOT EXISTS idx_oauth_connections_owner ON oauth_connections(owner_id, connected_at DESC)")

    def _connect(self):
        db = sqlite3.connect(self.database_path, timeout=5.0)
        db.row_factory = sqlite3.Row
        return db

    @staticmethod
    def _identifier(value, label):
        if not isinstance(value, str) or not _ID.fullmatch(value):
            raise ValueError(f"{label} is invalid")
        return value

    @staticmethod
    def _scopes(values):
        if not isinstance(values, (list, tuple)) or len(values) > 64:
            raise ValueError("scopes must be a bounded list")
        if any(not isinstance(s, str) or not _SCOPE.fullmatch(s) for s in values):
            raise ValueError("scope is invalid")
        if len(set(values)) != len(values):
            raise ValueError("duplicate scopes")
        return tuple(sorted(values))

    def register(self, *, owner_id, provider_id, account_id, scopes):
        owner_id = self._identifier(owner_id, "owner_id")
        provider_id = self._identifier(provider_id, "provider_id")
        account_id = self._identifier(account_id, "account_id")
        scopes = self._scopes(scopes)
        connected_at = int(self._clock())
        with self._connect() as db:
            db.execute("""
                INSERT INTO oauth_connections(owner_id, provider_id, account_id, scopes_json, connected_at)
                VALUES(?,?,?,?,?)
                ON CONFLICT(owner_id, provider_id, account_id) DO UPDATE SET
                    scopes_json=excluded.scopes_json, connected_at=excluded.connected_at
            """, (owner_id, provider_id, account_id, json.dumps(scopes), connected_at))
        return OAuthConnectionRecord(provider_id, account_id, scopes, connected_at)

    def list_for_owner(self, *, owner_id):
        owner_id = self._identifier(owner_id, "owner_id")
        with self._connect() as db:
            rows = db.execute("""
                SELECT provider_id, account_id, scopes_json, connected_at
                FROM oauth_connections WHERE owner_id=? ORDER BY connected_at DESC, provider_id, account_id
                LIMIT 100
            """, (owner_id,)).fetchall()
        records = []
        for row in rows:
            scopes = json.loads(row["scopes_json"])
            records.append(OAuthConnectionRecord(
                row["provider_id"], row["account_id"], self._scopes(scopes), row["connected_at"]
            ))
        return records

    def get(self, *, owner_id, provider_id, account_id):
        owner_id = self._identifier(owner_id, "owner_id")
        provider_id = self._identifier(provider_id, "provider_id")
        account_id = self._identifier(account_id, "account_id")
        with self._connect() as db:
            row = db.execute("""
                SELECT provider_id, account_id, scopes_json, connected_at
                FROM oauth_connections WHERE owner_id=? AND provider_id=? AND account_id=?
            """, (owner_id, provider_id, account_id)).fetchone()
        if row is None:
            return None
        return OAuthConnectionRecord(
            row["provider_id"], row["account_id"],
            self._scopes(json.loads(row["scopes_json"])), row["connected_at"]
        )

    def remove(self, *, owner_id, provider_id, account_id):
        owner_id = self._identifier(owner_id, "owner_id")
        provider_id = self._identifier(provider_id, "provider_id")
        account_id = self._identifier(account_id, "account_id")
        with self._connect() as db:
            cursor = db.execute("""
                DELETE FROM oauth_connections WHERE owner_id=? AND provider_id=? AND account_id=?
            """, (owner_id, provider_id, account_id))
            return cursor.rowcount == 1
