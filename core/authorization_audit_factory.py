"""Explicit SQLite/shared PostgreSQL authorization audit construction."""
from __future__ import annotations

from pathlib import Path
from typing import Any


def create_authorization_audit_repository(config: dict[str, Any]):
    if not isinstance(config, dict):
        raise TypeError("config must be a mapping")
    backend = config.get("storage_backend", "sqlite")
    if backend == "sqlite":
        path = config.get("authorization_audit_path")
        if path is None:
            workspace = config.get("workspace")
            if not isinstance(workspace, str) or not workspace.strip():
                raise ValueError("authorization_audit_workspace_required")
            path = str(Path(workspace).expanduser() / "authorization-audit.sqlite3")
        if not isinstance(path, str) or not path.strip():
            raise ValueError("authorization_audit_path_invalid")
        from core.authorization_audit import SQLiteAuthorizationAuditLog
        return SQLiteAuthorizationAuditLog(path)
    if backend == "postgres":
        database_url = config.get("database_url")
        if not isinstance(database_url, str) or not database_url.strip():
            raise ValueError("authorization_audit_database_url_required")
        from core.postgres_authorization_audit import PostgresAuthorizationAuditLog
        repository = PostgresAuthorizationAuditLog(
            database_url,
            min_pool_size=config.get("database_pool_min", 1),
            max_pool_size=config.get("database_pool_max", 10),
            pool_timeout=config.get("database_pool_timeout", 10.0),
        )
        try:
            repository.check_ready()
        except Exception as exc:
            repository.close()
            raise RuntimeError("authorization_audit_postgres_not_ready") from exc
        return repository
    raise ValueError("authorization_audit_storage_backend_invalid")


def close_authorization_audit_repository(repository) -> None:
    close = getattr(repository, "close", None)
    if not callable(close):
        raise TypeError("authorization_audit_repository_close_required")
    close()
