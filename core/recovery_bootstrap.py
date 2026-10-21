"""Opt-in-by-state startup hook for the durable sandbox recovery journal.

Recovery runs only when a journal already exists. An unreadable journal aborts
startup rather than allowing Yuki to operate while stale sandbox state is unknown.
"""
from __future__ import annotations

from pathlib import Path

from core.execution_journal_config import execution_journal_path, open_execution_journal
from core.startup_recovery import StartupRecoveryReport, recover_interrupted_executions


def recover_workspace_on_startup(workspace: str | Path) -> StartupRecoveryReport | None:
    """Reconcile an existing journal before the interactive runtime starts.

    No journal means no recovery work. A symlink or non-regular journal path is
    rejected. Ambiguous Docker state is reported as pending by the reconciler;
    journal/database failures propagate and prevent unsafe normal startup.
    """
    root = Path(workspace).expanduser().resolve()
    journal_path = execution_journal_path(root)
    if not journal_path.exists():
        return None
    if journal_path.is_symlink() or not journal_path.is_file():
        raise RuntimeError("Execution journal must be a regular, non-symlink file.")
    journal = open_execution_journal(root)
    return recover_interrupted_executions(journal)
