"""Read-only startup diagnostics for Yuki's private SQLite stores.

The report deliberately exposes component labels and remediation guidance, not
filesystem paths, database contents, SQL errors, or credentials. It never
creates, repairs, migrates, or overwrites a database.
"""
from __future__ import annotations

import os
import sqlite3
import stat
from pathlib import Path
from typing import Mapping


def inspect_sqlite_store(component: str, database_path: str | Path) -> dict[str, str]:
    """Return a sanitized health result for one SQLite store."""
    if not isinstance(component, str) or not component.strip():
        raise ValueError("component_name_required")
    path = Path(database_path).expanduser()
    try:
        info = path.lstat()
    except FileNotFoundError:
        return {
            "component": component,
            "status": "missing",
            "action": "The store has not been created yet; normal first-run initialization is permitted.",
        }
    except OSError:
        return {
            "component": component,
            "status": "unavailable",
            "action": "Check the storage device and permissions. Preserve existing files and retry.",
        }

    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        return {
            "component": component,
            "status": "unsafe",
            "action": "The database path is not a private regular file. Do not open or replace it; inspect the storage configuration.",
        }
    if info.st_size == 0:
        return {
            "component": component,
            "status": "empty",
            "action": "An existing database is empty. Preserve it for recovery; do not initialize over it.",
        }

    try:
        uri = path.resolve(strict=True).as_uri() + "?mode=ro"
        connection = sqlite3.connect(uri, uri=True, timeout=5)
        try:
            result = connection.execute("PRAGMA quick_check").fetchall()
        finally:
            connection.close()
    except (OSError, sqlite3.DatabaseError, ValueError):
        return {
            "component": component,
            "status": "corrupt_or_unreadable",
            "action": "Yuki could not validate this database. Preserve the original and restore from a verified backup or seek recovery.",
        }

    if not result or any(not row or row[0] != "ok" for row in result):
        return {
            "component": component,
            "status": "corrupt",
            "action": "SQLite integrity validation failed. Preserve the original and restore from a verified backup or seek recovery.",
        }
    return {
        "component": component,
        "status": "healthy",
        "action": "No recovery action required.",
    }


def build_sqlite_recovery_report(stores: Mapping[str, str | Path]) -> dict:
    """Inspect named stores without modifying them and summarize startup safety."""
    results = [
        inspect_sqlite_store(component, path)
        for component, path in stores.items()
    ]
    blocking = {"unsafe", "empty", "corrupt", "corrupt_or_unreadable", "unavailable"}
    blocked = any(item["status"] in blocking for item in results)
    return {
        "schema_version": 1,
        "startup_safe": not blocked,
        "stores": results,
        "summary": (
            "One or more existing data stores need attention. Yuki must not initialize over or modify them."
            if blocked
            else "All existing data stores passed read-only checks."
        ),
    }

class SQLiteRecoveryRequired(RuntimeError):
    """Startup must stop because an existing store needs owner attention."""

    def __init__(self, report: dict):
        self.report = report
        actions = "; ".join(
            f"{item['component']}: {item['action']}"
            for item in report["stores"]
            if item["status"] not in {"healthy", "missing"}
        )
        super().__init__(actions or report["summary"])


def require_healthy_sqlite_stores(stores: Mapping[str, str | Path]) -> dict:
    """Return a health report or stop startup before any store is initialized."""
    report = build_sqlite_recovery_report(stores)
    if not report["startup_safe"]:
        raise SQLiteRecoveryRequired(report)
    return report


def format_sqlite_recovery_report(report: dict) -> str:
    """Render an owner-facing, path-free startup recovery explanation."""
    if not isinstance(report, dict) or not isinstance(report.get("stores"), list):
        raise ValueError("recovery_report_invalid")
    lines = ["Yuki paused startup to protect existing data."]
    for item in report["stores"]:
        component = item.get("component", "unknown")
        status = item.get("status", "unknown")
        if status in {"healthy", "missing"}:
            continue
        action = item.get("action", "Preserve the data and inspect storage before retrying.")
        lines.append(f"{component}: {status}. {action}")
    if len(lines) == 1:
        lines.append(report.get("summary", "Review the local data store before retrying."))
    return "\\n".join(lines)
