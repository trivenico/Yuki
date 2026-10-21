"""Journal-integrated execution of staged proposal checks.

This adapter persists intent before launching sandbox processes and records a
terminal result only after the executor returns (which includes confirmed
container cleanup). It deliberately never replays an existing execution ID.
"""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Sequence

_MAX_CHECKS = 32
_MAX_JOURNAL_OUTPUT_BYTES = 48 * 1024


def _clip_utf8(value: str, budget: int) -> tuple[str, int]:
    """Return valid UTF-8 text fitting budget and the bytes consumed."""
    encoded = value.encode("utf-8", errors="replace")
    if len(encoded) <= budget:
        return value, len(encoded)
    clipped = encoded[:max(0, budget)].decode("utf-8", errors="ignore")
    return clipped, len(clipped.encode("utf-8"))


def _bounded_result(checks: Sequence[SandboxResult]) -> dict:
    """Keep aggregate journal output below the DB result cap with room for metadata."""
    remaining = _MAX_JOURNAL_OUTPUT_BYTES
    summaries = []
    for item in checks:
        stdout, used = _clip_utf8(item.stdout, remaining)
        remaining -= used
        stderr, used = _clip_utf8(item.stderr, remaining)
        remaining -= used
        was_clipped = stdout != item.stdout or stderr != item.stderr
        summaries.append({
            "returncode": int(item.returncode),
            "timed_out": bool(item.timed_out),
            "output_truncated": bool(item.output_truncated or was_clipped),
            "stdout": stdout,
            "stderr": stderr,
        })
    return {"checks": summaries, "journal_output_truncated": remaining == 0}

from core.execution_journal import ExecutionJournalError, ExecutionRecord, SQLiteExecutionJournal
from core.execution_journal_config import open_execution_journal
from core.proposal_test_runner import (
    ProposalTestRunError,
    _managed_workspace,
    _validate_test_command,
    run_proposal_checks,
)
from core.sandbox_executor import SandboxResult
from core.sandbox_policy import SandboxPolicy


class JournaledProposalRunError(RuntimeError):
    """A journaled proposal run could not be safely completed."""


@dataclass(frozen=True)
class JournaledProposalResult:
    execution: ExecutionRecord
    checks: tuple[SandboxResult, ...]


def run_journaled_proposal_checks(
    *,
    journal: SQLiteExecutionJournal | None = None,
    execution_id: str,
    proposal_sha256: str,
    workspace: str | os.PathLike[str],
    parent_dir: str | os.PathLike[str],
    policy: SandboxPolicy,
    image: str,
    commands: Sequence[Sequence[str]],
) -> JournaledProposalResult:
    """Run once under a durable ID; duplicate/recovered IDs are never replayed."""
    if journal is None:
        try:
            journal = open_execution_journal(parent_dir)
        except (OSError, ValueError) as exc:
            raise JournaledProposalRunError("Could not open the canonical execution journal.") from exc
    if not isinstance(proposal_sha256, str) or len(proposal_sha256) != 64:
        raise JournaledProposalRunError("A SHA-256 proposal digest is required.")
    try:
        managed = _managed_workspace(workspace, parent_dir)
        if not isinstance(commands, (list, tuple)) or not commands:
            raise ProposalTestRunError("At least one explicit check is required.")
        if len(commands) > _MAX_CHECKS:
            raise ProposalTestRunError("At most 32 checks may be scheduled per execution.")
        for command in commands:
            _validate_test_command(command)
        policy.validate()
    except Exception as exc:
        raise JournaledProposalRunError("Preflight validation failed before execution.") from exc

    context = {
        "proposal_sha256": proposal_sha256,
        "workspace": str(managed),
        "image": image,
        "policy": {
            "network_mode": policy.network_mode,
            "capabilities": sorted(policy.capabilities),
            "timeout_seconds": policy.timeout_seconds,
            "memory_mb": policy.memory_mb,
            "cpu_millis": policy.cpu_millis,
            "max_processes": policy.max_processes,
            "max_output_bytes": policy.max_output_bytes,
        },
        "commands": [list(command) for command in commands],
    }
    try:
        record = journal.create(execution_id, context)
    except ExecutionJournalError as exc:
        raise JournaledProposalRunError("Execution journal rejected this run.") from exc
    if record.state != "created":
        raise JournaledProposalRunError(
            "Execution ID already started or completed; automatic replay is forbidden."
        )

    try:
        journal.transition(execution_id, "running")
        checks = run_proposal_checks(
            workspace=managed,
            parent_dir=parent_dir,
            policy=policy,
            image=image,
            commands=commands,
            execution_id=execution_id,
        )
        last = checks[-1]
        if last.timed_out:
            terminal_state = "timed_out"
        elif last.returncode != 0:
            terminal_state = "failed"
        else:
            terminal_state = "succeeded"
        record = journal.transition(
            execution_id,
            terminal_state,
            result=_bounded_result(checks),
            cleanup_confirmed=True,
        )
        return JournaledProposalResult(record, checks)
    except Exception as exc:
        # A thrown executor error can mean cleanup was not confirmed. Do not
        # claim failure is safely terminal; require explicit cleanup recovery.
        try:
            current = journal.get(execution_id)
            if current.state == "running":
                journal.transition(
                    execution_id,
                    "cleanup_required",
                    result={"error_type": type(exc).__name__},
                    cleanup_confirmed=False,
                )
        except Exception:
            pass
        if isinstance(exc, JournaledProposalRunError):
            raise
        raise JournaledProposalRunError(
            "Proposal checks did not complete cleanly; cleanup reconciliation is required."
        ) from exc
