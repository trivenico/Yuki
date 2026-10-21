"""Canonical factory for Yuki's durable sandbox execution journal."""
from __future__ import annotations

from pathlib import Path

from core.execution_journal import SQLiteExecutionJournal

JOURNAL_FILENAME = "yuki_execution_journal.sqlite3"


def execution_journal_path(workspace: str | Path) -> Path:
    """Return the single canonical journal path for execution and recovery."""
    root = Path(workspace).expanduser().resolve()
    if not root.exists() or not root.is_dir():
        raise ValueError("workspace must be an existing directory")
    return root / JOURNAL_FILENAME


def open_execution_journal(workspace: str | Path) -> SQLiteExecutionJournal:
    """Open the canonical journal used by both execution and startup recovery."""
    return SQLiteExecutionJournal(str(execution_journal_path(workspace)))
