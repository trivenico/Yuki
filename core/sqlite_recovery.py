"""Read-only startup diagnostics for Yuki's private SQLite stores.

Reports expose component labels and remediation guidance, never database paths,
contents, SQL errors, or credentials. Inspection never repairs or overwrites data.
"""
from __future__ import annotations

import sqlite3
import stat
from pathlib import Path
from typing import Any, Mapping


def inspect_sqlite_store(component: str, database_path: str | Path) -> dict[str, str]:
    if (not isinstance(component, str) or not component.strip() or len(component) > 80
            or any(ord(char) < 32 for char in component)):
        raise ValueError("component_name_invalid")
    path = Path(database_path).expanduser()
    try:
        info = path.lstat()
    except FileNotFoundError:
        return {"component": component, "status": "missing", "action": "The store has not been created yet; normal first-run initialization is permitted."}
    except OSError:
        return {"component": component, "status": "unavailable", "action": "Check the storage device and permissions. Preserve existing files and retry."}

    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        return {"component": component, "status": "unsafe", "action": "The database path is not a private regular file. Do not open or replace it; inspect storage configuration."}
    if info.st_size == 0:
        return {"component": component, "status": "empty", "action": "An existing database is empty. Preserve it for recovery; do not initialize over it."}

    try:
        uri = path.resolve(strict=True).as_uri() + "?mode=ro"
        connection = sqlite3.connect(uri, uri=True, timeout=5)
        try:
            result = connection.execute("PRAGMA quick_check").fetchall()
        finally:
            connection.close()
    except (OSError, sqlite3.DatabaseError, ValueError):
        return {"component": component, "status": "corrupt_or_unreadable", "action": "Yuki could not validate this database. Preserve the original and restore from a verified backup or seek recovery."}

    if not result or any(not row or row[0] != "ok" for row in result):
        return {"component": component, "status": "corrupt", "action": "SQLite integrity validation failed. Preserve the original and restore from a verified backup or seek recovery."}
    return {"component": component, "status": "healthy", "action": "No recovery action required."}


def build_sqlite_recovery_report(
    stores: Mapping[str, str | Path], *, additional_checks: tuple[dict[str, str], ...] = (),
) -> dict:
    if not isinstance(stores, Mapping):
        raise TypeError("stores_must_be_a_mapping")
    if not isinstance(additional_checks, tuple):
        raise TypeError("additional_checks_must_be_a_tuple")
    results = [inspect_sqlite_store(component, path) for component, path in stores.items()]
    allowed_statuses = {
        "missing", "healthy", "unsafe", "empty", "invalid", "corrupt",
        "corrupt_or_unreadable", "unavailable",
    }
    for item in additional_checks:
        if not isinstance(item, dict) or set(item) != {"component", "status", "action"}:
            raise ValueError("additional_check_schema_invalid")
        component, status, action = item["component"], item["status"], item["action"]
        if (not isinstance(component, str) or not component.strip() or len(component) > 80
                or any(ord(char) < 32 for char in component)):
            raise ValueError("additional_check_component_invalid")
        if status not in allowed_statuses:
            raise ValueError("additional_check_status_invalid")
        if (not isinstance(action, str) or not action.strip() or len(action) > 500
                or any(ord(char) < 32 and char not in "\\n\\t" for char in action)):
            raise ValueError("additional_check_action_invalid")
        results.append({"component": component, "status": status, "action": action})
    blocking = {"unsafe", "empty", "invalid", "corrupt", "corrupt_or_unreadable", "unavailable"}
    blocked = any(item["status"] in blocking for item in results)
    return {
        "schema_version": 1,
        "startup_safe": not blocked,
        "stores": results,
        "summary": (
            "One or more existing data stores need attention. Yuki must not initialize over or modify them."
            if blocked else "All existing data stores passed read-only checks."
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


def require_healthy_sqlite_stores(
    stores: Mapping[str, str | Path], *, additional_checks: tuple[dict[str, str], ...] = (),
) -> dict:
    report = build_sqlite_recovery_report(stores, additional_checks=additional_checks)
    if not report["startup_safe"]:
        raise SQLiteRecoveryRequired(report)
    return report


def format_sqlite_recovery_report(report: dict) -> str:
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
    return "\n".join(lines)


def inspect_encrypted_memory_store(
    memory_path: str | Path, keyring_module: Any, *, keyring_account: str = "default",
) -> dict[str, str]:
    """Authenticate existing encrypted memory without changing its ciphertext."""
    path = Path(memory_path).expanduser()
    try:
        info = path.lstat()
    except FileNotFoundError:
        return {"component": "encrypted_memory", "status": "missing", "action": "No encrypted memory file exists yet; first-use initialization is permitted."}
    except OSError:
        return {"component": "encrypted_memory", "status": "unavailable", "action": "Check the memory storage device and permissions. Preserve existing files and retry."}
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        return {"component": "encrypted_memory", "status": "unsafe", "action": "The encrypted memory path is not a private regular file. Preserve it and inspect storage configuration."}
    if info.st_size == 0 or info.st_size > 10_000_000:
        return {"component": "encrypted_memory", "status": "invalid", "action": "The existing encrypted memory file has an invalid size. Preserve it for recovery."}
    try:
        from companion.secure_memory import EncryptedMemoryStore
        EncryptedMemoryStore(path, keyring_module=keyring_module, keyring_account=keyring_account).list()
    except Exception:
        return {"component": "encrypted_memory", "status": "corrupt_or_unreadable", "action": "Yuki could not authenticate encrypted memory. Preserve the ciphertext and recover the original OS keyring key."}
    return {"component": "encrypted_memory", "status": "healthy", "action": "Encrypted memory authentication succeeded."}
