"""Explicit single-host/shared backend construction for the security monitor."""
from __future__ import annotations

from pathlib import Path
import math
from typing import Any, Callable


def create_security_event_monitor(
    config: dict[str, Any],
    *,
    authorize_operator: Callable[[str], bool],
):
    if not isinstance(config, dict):
        raise TypeError("config must be a mapping")
    if not callable(authorize_operator):
        raise TypeError("security_monitor_authorizer_required")
    threshold = config.get("security_failure_threshold", 5)
    window = config.get("security_window_seconds", 300)
    if type(threshold) is not int or not 2 <= threshold <= 1000:
        raise ValueError("security_failure_threshold_invalid")
    if type(window) is not int or not 1 <= window <= 86400:
        raise ValueError("security_window_invalid")

    backend = config.get("storage_backend", "sqlite")
    if backend == "sqlite":
        workspace = config.get("workspace")
        if not isinstance(workspace, str) or not workspace.strip():
            raise ValueError("security_monitor_workspace_required")
        from core.security_event_monitor import SecurityEventMonitor

        return SecurityEventMonitor(
            str(Path(workspace).expanduser() / "security-events.sqlite3"),
            authorize_operator=authorize_operator,
            failure_threshold=threshold,
            window_seconds=window,
        )

    if backend == "postgres":
        database_url = config.get("database_url")
        if not isinstance(database_url, str) or not database_url.strip():
            raise ValueError("security_monitor_database_url_required")
        min_pool_size = config.get("database_pool_min", 1)
        max_pool_size = config.get("database_pool_max", 10)
        pool_timeout = config.get("database_pool_timeout", 10.0)
        if (type(min_pool_size) is not int or type(max_pool_size) is not int
                or not 1 <= min_pool_size <= max_pool_size <= 50):
            raise ValueError("security_monitor_pool_configuration_invalid")
        if (isinstance(pool_timeout, bool) or not isinstance(pool_timeout, (int, float))
                or not math.isfinite(pool_timeout) or not 1 <= pool_timeout <= 120):
            raise ValueError("security_monitor_pool_timeout_invalid")
        from core.postgres_security_event_monitor import PostgresSecurityEventMonitor

        monitor = PostgresSecurityEventMonitor(
            database_url,
            authorize_operator=authorize_operator,
            failure_threshold=threshold,
            window_seconds=window,
            min_pool_size=min_pool_size,
            max_pool_size=max_pool_size,
            pool_timeout=float(pool_timeout),
        )
        try:
            if monitor.check_ready() is not True:
                raise RuntimeError("security_monitor_schema_not_ready")
        except Exception as exc:
            monitor.close()
            raise RuntimeError(
                "PostgreSQL security monitor is unavailable or not migrated"
            ) from exc
        return monitor

    raise ValueError("storage_backend must be sqlite or postgres")


def close_security_event_monitor(monitor) -> None:
    close = getattr(monitor, "close", None)
    if not callable(close):
        raise TypeError("security_monitor_close_required")
    close()
