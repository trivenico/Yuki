"""Trusted construction of workload public-key registries.

The selected storage backend is explicit deployment configuration. A PostgreSQL
selection never falls back to a local SQLite file when the database is absent,
unavailable, or missing its security migrations.
"""
from __future__ import annotations

from pathlib import Path
import math
from typing import Any, Callable


def create_workload_key_registry(
    config: dict[str, Any],
    *,
    authorize_admin: Callable[[str], bool],
):
    """Build and validate the configured key registry for host dependency injection.

    authorize_admin must be a trusted host callback. It must not derive
    administrator identity or privileges from task payloads or model output.
    """
    if not isinstance(config, dict):
        raise TypeError("config must be a mapping")
    if not callable(authorize_admin):
        raise TypeError("trusted_workload_key_admin_authorizer_required")

    backend = config.get("storage_backend", "sqlite")
    if backend == "sqlite":
        workspace = config.get("workspace")
        if not isinstance(workspace, str) or not workspace.strip():
            raise ValueError("workload_key_registry_workspace_required")
        from core.workload_key_registry import SQLiteWorkloadKeyRegistry

        database_path = Path(workspace).expanduser() / "security-workload-keys.sqlite3"
        return SQLiteWorkloadKeyRegistry(
            str(database_path),
            authorize_admin=authorize_admin,
        )

    if backend == "postgres":
        database_url = config.get("database_url")
        if not isinstance(database_url, str) or not database_url.strip():
            raise ValueError("workload_key_registry_database_url_required")
        min_pool_size = config.get("database_pool_min", 1)
        max_pool_size = config.get("database_pool_max", 10)
        pool_timeout = config.get("database_pool_timeout", 10.0)
        if (
            type(min_pool_size) is not int
            or type(max_pool_size) is not int
            or not 1 <= min_pool_size <= max_pool_size <= 50
        ):
            raise ValueError("workload_key_registry_pool_configuration_invalid")
        if (isinstance(pool_timeout, bool) or not isinstance(pool_timeout, (int, float))
                or not math.isfinite(pool_timeout) or not 1 <= pool_timeout <= 120):
            raise ValueError("workload_key_registry_pool_timeout_invalid")

        from core.postgres_workload_key_registry import PostgresWorkloadKeyRegistry

        registry = PostgresWorkloadKeyRegistry(
            database_url,
            authorize_admin=authorize_admin,
            min_pool_size=min_pool_size,
            max_pool_size=max_pool_size,
            pool_timeout=float(pool_timeout),
        )
        try:
            ready = registry.check_ready()
            if ready is not True:
                raise RuntimeError("workload_key_registry_schema_not_ready")
        except Exception as exc:
            registry.close()
            raise RuntimeError(
                "PostgreSQL workload key registry is unavailable or not migrated"
            ) from exc
        return registry

    raise ValueError("storage_backend must be sqlite or postgres")


def close_workload_key_registry(registry) -> None:
    """Close registry resources during trusted host shutdown."""
    close = getattr(registry, "close", None)
    if not callable(close):
        raise TypeError("workload_key_registry_close_required")
    close()
