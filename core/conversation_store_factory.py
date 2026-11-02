"""Conversation persistence backend selection."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from core.conversation_store import ConversationStore


def create_conversation_store(config: dict[str, Any]):
    """Construct the configured store; PostgreSQL dependencies remain optional."""
    if not isinstance(config, dict):
        raise TypeError("config must be a mapping.")
    backend = config.get("storage_backend", "sqlite")
    if backend == "sqlite":
        workspace = config.get("workspace")
        if not isinstance(workspace, str) or not workspace.strip():
            raise ValueError("workspace is required for SQLite storage.")
        path = Path(workspace).expanduser() / "conversations.sqlite3"
        return ConversationStore(path)
    if backend == "postgres":
        database_url = config.get("database_url")
        if not isinstance(database_url, str) or not database_url.strip():
            raise ValueError("database_url is required for PostgreSQL storage.")
        from core.postgres_conversation_store import PostgresConversationStore
        return PostgresConversationStore(
            database_url,
            min_pool_size=config.get("database_pool_min", 1),
            max_pool_size=config.get("database_pool_max", 10),
        )
    raise ValueError("storage_backend must be sqlite or postgres.")
