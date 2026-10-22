"""Supervised test execution for staged self-improvement proposals.

Only known test/lint entry points are accepted. This runner does not apply
proposals, commit, merge, or deploy. Use no-network policies and a read-only
workspace unless writes were explicitly authorized.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Sequence

from core.sandbox_executor import SandboxExecutionError, SandboxResult, execute_docker_sandbox
from core.sandbox_policy import SandboxPolicy
from core.staged_integrity import StagedIntegrityError, verify_staged_workspace


class ProposalTestRunError(RuntimeError):
    """A proposal test run was rejected or could not be completed safely."""


def _managed_workspace(workspace: str | os.PathLike[str], parent_dir: str | os.PathLike[str]) -> Path:
    parent = Path(parent_dir)
    candidate = Path(workspace)
    try:
        if parent.is_symlink() or not parent.is_dir():
            raise ProposalTestRunError("Workspace parent must be a non-symlink directory.")
        resolved_parent = parent.resolve(strict=True)
        if candidate.is_symlink() or not candidate.is_dir():
            raise ProposalTestRunError("Workspace must be an existing non-symlink directory.")
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise ProposalTestRunError("Workspace path could not be verified.") from exc
    if resolved.parent != resolved_parent or not resolved.name.startswith("yuki-improvement-"):
        raise ProposalTestRunError("Workspace must be a direct managed child of the configured parent.")
    return resolved


def _validate_test_command(command: Sequence[str]) -> None:
    if not isinstance(command, (list, tuple)) or not command:
        raise ProposalTestRunError("Each check must be a non-empty argument vector.")
    if any(not isinstance(arg, str) or not arg or "\x00" in arg for arg in command):
        raise ProposalTestRunError("Check arguments must be non-empty NUL-free strings.")
    if len(command) > 128 or any(len(arg) > 4096 for arg in command):
        raise ProposalTestRunError("Check command exceeds configured argument bounds.")
    allowed = (
        command[0] == "pytest"
        or (command[0] == "ruff" and len(command) >= 2 and command[1] == "check")
        or (
            command[0] in {"python", "python3"} and len(command) >= 3
            and command[1] == "-m" and command[2] in {"pytest", "unittest", "ruff"}
        )
    )
    if not allowed:
        raise ProposalTestRunError("Only pytest, unittest, and ruff check entry points are permitted.")
    if "-c" in command or any(arg in {"sh", "bash", "cmd", "powershell", "pwsh"} for arg in command):
        raise ProposalTestRunError("Shell and inline-code execution are not permitted.")


def run_proposal_checks(
    *,
    workspace: str | os.PathLike[str],
    parent_dir: str | os.PathLike[str],
    policy: SandboxPolicy,
    image: str,
    commands: Sequence[Sequence[str]],
    execution_id: str | None = None,
) -> tuple[SandboxResult, ...]:
    """Run ordered checks in the same disposable workspace, stopping on failure."""
    managed = _managed_workspace(workspace, parent_dir)
    try:
        verify_staged_workspace(managed)
    except (StagedIntegrityError, OSError, ValueError) as exc:
        raise ProposalTestRunError("Candidate integrity must pass before any sandbox execution.") from exc
    if not isinstance(commands, (list, tuple)) or not commands:
        raise ProposalTestRunError("At least one explicit check is required.")
    for command in commands:
        _validate_test_command(command)

    try:
        policy.validate()
    except Exception as exc:
        raise ProposalTestRunError("Sandbox policy was rejected.") from exc

    results: list[SandboxResult] = []
    for command in commands:
        try:
            result = execute_docker_sandbox(
                policy,
                image=image,
                workspace=str(managed),
                command=command,
                execution_id=execution_id,
            )
        except SandboxExecutionError as exc:
            raise ProposalTestRunError("Sandbox check failed to complete safely.") from exc
        results.append(result)
        if result.timed_out or result.returncode != 0:
            break
    return tuple(results)
