"""PostgreSQL-backed conversation persistence with bounded connection pooling.

The public API intentionally mirrors ConversationStore so the conversation
service can use either backend without backend-specific branching.
"""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit
import re
from typing import Any

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from core.conversation_store import (
    ConversationStore,
    ConversationConflict,
    ConversationNotFound,
)


class PostgresConversationStore(ConversationStore):
    """ConversationStore implementation using PostgreSQL and psycopg pooling."""

    def __init__(
        self,
        database_url: str,
        *,
        min_pool_size: int = 1,
        max_pool_size: int = 10,
        pool_timeout: float = 10.0,
    ):
        if not isinstance(database_url, str) or not database_url.strip() or len(database_url) > 4096:
            raise ValueError("database_url must be non-empty PostgreSQL connection text.")
        parsed = urlsplit(database_url)
        if parsed.scheme not in {"postgres", "postgresql"} or not parsed.hostname:
            raise ValueError("database_url must use postgres:// or postgresql://.")
        if type(min_pool_size) is not int or type(max_pool_size) is not int:
            raise ValueError("pool sizes must be integers.")
        if not 1 <= min_pool_size <= max_pool_size <= 50:
            raise ValueError("pool sizes must satisfy 1 <= min_pool_size <= max_pool_size <= 50.")
        if not isinstance(pool_timeout, (int, float)) or isinstance(pool_timeout, bool) or not 1 <= pool_timeout <= 120:
            raise ValueError("pool_timeout must be between 1 and 120 seconds.")

        self.database_url = database_url
        self.pool = ConnectionPool(
            conninfo=database_url,
            min_size=min_pool_size,
            max_size=max_pool_size,
            timeout=float(pool_timeout),
            kwargs={"row_factory": dict_row, "autocommit": True},
            open=True,
        )
        try:
            self.pool.wait(timeout=float(pool_timeout))
            self._initialize_schema()
        except Exception:
            self.pool.close()
            raise

    @staticmethod
    def _postgres_sql(statement: str) -> str:
        statement = statement.replace("BEGIN IMMEDIATE", "BEGIN")
        # Store SQL uses qmark placeholders and contains no question-mark
        # literals; psycopg uses %s placeholders.
        return statement.replace("?", "%s")

    @contextmanager
    def _connect(self):
        with self.pool.connection() as db:
            yield _PostgresConnection(db)

    def _initialize_schema(self) -> None:
        statements = [
            """CREATE TABLE IF NOT EXISTS conversations (
                conversation_id TEXT PRIMARY KEY,
                owner_id TEXT NOT NULL,
                title TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                version INTEGER NOT NULL DEFAULT 0 CHECK(version >= 0),
                archived BOOLEAN NOT NULL DEFAULT FALSE
            )""",
            """CREATE INDEX IF NOT EXISTS idx_conversations_owner_updated
                ON conversations(owner_id, archived, updated_at DESC)""",
            """CREATE TABLE IF NOT EXISTS conversation_messages (
                message_id TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL REFERENCES conversations(conversation_id) ON DELETE CASCADE,
                sequence INTEGER NOT NULL CHECK(sequence > 0),
                role TEXT NOT NULL CHECK(role IN ('user', 'assistant')),
                content TEXT NOT NULL,
                idempotency_key TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(conversation_id, sequence),
                UNIQUE(conversation_id, idempotency_key)
            )""",
            """CREATE INDEX IF NOT EXISTS idx_conversation_messages_order
                ON conversation_messages(conversation_id, sequence)""",
            """CREATE TABLE IF NOT EXISTS conversation_generation_leases (
                conversation_id TEXT PRIMARY KEY REFERENCES conversations(conversation_id) ON DELETE CASCADE,
                owner_id TEXT NOT NULL,
                lease_token TEXT NOT NULL,
                expires_at DOUBLE PRECISION NOT NULL
            )""",
            """CREATE INDEX IF NOT EXISTS idx_generation_leases_expiry
                ON conversation_generation_leases(expires_at)""",
            """CREATE TABLE IF NOT EXISTS conversation_generation_runs (
                generation_id TEXT PRIMARY KEY,
                conversation_id TEXT NOT NULL REFERENCES conversations(conversation_id) ON DELETE CASCADE,
                owner_id TEXT NOT NULL,
                request_id TEXT NOT NULL,
                user_message_id TEXT NOT NULL UNIQUE REFERENCES conversation_messages(message_id) ON DELETE CASCADE,
                status TEXT NOT NULL CHECK(status IN ('running', 'completed', 'interrupted', 'failed', 'cancelled')),
                checkpoint TEXT NOT NULL DEFAULT '{}',
                started_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                finished_at TEXT,
                UNIQUE(conversation_id, request_id)
            )""",
            """CREATE INDEX IF NOT EXISTS idx_generation_runs_owner_status
                ON conversation_generation_runs(owner_id, status, updated_at)""",
        ]
        with self._connect() as db:
            for statement in statements:
                db.execute(statement)

    def close(self) -> None:
        """Close the pool during application shutdown."""
        self.pool.close()


class _PostgresConnection:
    """Small DB-API compatibility layer for the shared store implementation."""

    def __init__(self, connection):
        self._connection = connection

    def execute(self, statement: str, parameters: tuple[Any, ...] = ()):
        return self._connection.execute(
            PostgresConversationStore._postgres_sql(statement), parameters
        )

    def commit(self) -> None:
        self._connection.commit()

    def rollback(self) -> None:
        self._connection.rollback()
