"""PostgreSQL-backed conversation persistence with bounded connection pooling.

The public API intentionally mirrors ConversationStore so the conversation
service can use either backend without backend-specific branching.
"""
from __future__ import annotations

from contextlib import contextmanager
from urllib.parse import urlsplit
from psycopg.conninfo import conninfo_to_dict
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
        try:
            conninfo = conninfo_to_dict(database_url)
        except Exception as exc:
            raise ValueError("database_url must be valid PostgreSQL connection text.") from exc
        if database_url.lstrip().startswith(("postgres://", "postgresql://")):
            parsed = urlsplit(database_url)
            if parsed.scheme not in {"postgres", "postgresql"}:
                raise ValueError("database_url must use PostgreSQL connection syntax.")
            try:
                parsed.port
            except ValueError as exc:
                raise ValueError("database_url contains an invalid port.") from exc
        if not (
            (conninfo.get("host") or conninfo.get("hostaddr") or conninfo.get("service"))
            and conninfo.get("dbname")
        ):
            raise ValueError("database_url must identify a PostgreSQL host and database.")
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
            self._verify_schema()
        except Exception:
            self.pool.close()
            raise

    @staticmethod
    def _postgres_sql(statement: str) -> str:
        statement = statement.replace("BEGIN IMMEDIATE", "BEGIN")
        statement = statement.replace("archived=1", "archived=TRUE")
        statement = statement.replace("archived=0", "archived=FALSE")
        # Store SQL uses qmark placeholders and contains no question-mark
        # literals; psycopg uses %s placeholders.
        return statement.replace("?", "%s")

    @contextmanager
    def _connect(self):
        with self.pool.connection() as db:
            yield _PostgresConnection(db)

    def _verify_schema(self) -> None:
        """Verify migrations provisioned every required relation; never perform runtime DDL."""
        required = (
            "conversations",
            "conversation_messages",
            "conversation_generation_leases",
            "conversation_generation_runs",
        )
        with self._connect() as db:
            missing = []
            for table in required:
                row = db.execute(
                    "SELECT to_regclass(%s) AS relation",
                    (f"public.{table}",),
                ).fetchone()
                if not row or row["relation"] is None:
                    missing.append(table)
        if missing:
            raise RuntimeError(
                "PostgreSQL conversation schema is incomplete "
                f"(missing: {', '.join(missing)}). "
                "Run the approved deployment migration command: yuki-db-migrate --plan, "
                "then yuki-db-migrate --apply."
            )

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
