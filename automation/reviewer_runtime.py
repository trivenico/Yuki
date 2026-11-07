"""Opt-in composition for isolated reviewer execution."""
from __future__ import annotations

from typing import Callable

from automation.docker_reviewer_executor import (
    DockerReviewerExecutor, ReviewerContainerCommand,
)
from automation.reviewer_execution import ReviewerExecutionAdapter
from automation.reviewer_ledger import ReviewerTaskLedger


def build_isolated_reviewer_adapter(
    ledger: ReviewerTaskLedger,
    *,
    evidence_reader: Callable[[str, str], bytes],
    image: str,
    command_argv: tuple[str, ...],
    timeout_seconds: int = 180,
    docker_binary: str = "docker",
    memory_limit: str = "1g",
    cpu_limit: str = "1",
    pids_limit: int = 64,
) -> ReviewerExecutionAdapter:
    """Compose the host ledger/evidence boundary with a Docker-only executor.

    The caller must construct the ledger with trusted authorization, signing,
    and session authorities. The container command must implement protocol v1.
    No credentials or ledger objects are passed to the container.
    """
    if not isinstance(ledger, ReviewerTaskLedger):
        raise TypeError("trusted_reviewer_ledger_required")
    if not callable(evidence_reader):
        raise TypeError("trusted_evidence_reader_required")
    command = ReviewerContainerCommand(
        argv=command_argv, timeout_seconds=timeout_seconds,
    )
    executor = DockerReviewerExecutor(
        image=image, command=command, docker_binary=docker_binary,
        memory_limit=memory_limit, cpu_limit=cpu_limit, pids_limit=pids_limit,
    )
    return ReviewerExecutionAdapter(ledger, evidence_reader=evidence_reader), executor


__all__ = ["build_isolated_reviewer_adapter"]
