"""Idempotent, deterministic budget allocation primitives for agent plans.

This module deliberately does not dispatch tasks. It bridges coordinator retries
and the immutable ExecutionBudgetStore while keeping logical task identities
stable before workflow task IDs exist.
"""
from __future__ import annotations

from typing import Mapping
import hashlib

from core.reasoning.execution_budget_store import (
    ExecutionBudgetSnapshot,
    ExecutionBudgetStore,
    ExecutionBudgetStoreError,
)


class AgentBudgetAllocationError(RuntimeError):
    """A durable plan budget is missing, inconsistent, or cannot be allocated."""


class AgentBudgetLifecycle:
    """Deterministic budget identities and verify-after-retry allocation."""

    def __init__(self, store: ExecutionBudgetStore):
        if not isinstance(store, ExecutionBudgetStore):
            raise TypeError("store must be an ExecutionBudgetStore")
        self.store = store

    @staticmethod
    def _identity(kind: str, *parts: str) -> str:
        if any(not isinstance(part, str) or not part or "\x00" in part for part in parts):
            raise ValueError("budget identity parts must be non-empty text")
        raw = "\x00".join((kind, *parts)).encode("utf-8")
        return "yb-" + hashlib.sha256(raw).hexdigest()[:40]

    @staticmethod
    def _task_identity(kind: str, *parts: str) -> str:
        raw = "\x00".join((kind, *parts)).encode("utf-8")
        return "yt-" + hashlib.sha256(raw).hexdigest()[:40]

    @staticmethod
    def _verify(snapshot: ExecutionBudgetSnapshot, *, budget_id: str,
                task_id: str, parent_budget_id: str | None,
                policy_version: str, limits: Mapping[str, int]) -> ExecutionBudgetSnapshot:
        expected = dict(limits)
        if (
            snapshot.budget_id != budget_id
            or snapshot.task_id != task_id
            or snapshot.parent_budget_id != parent_budget_id
            or snapshot.policy_version != policy_version
            or snapshot.limits != expected
        ):
            raise AgentBudgetAllocationError("existing_budget_snapshot_does_not_match_request")
        return snapshot

    def ensure_plan_budget(self, *, plan_id: str, limits: Mapping[str, int],
                           policy_version: str) -> ExecutionBudgetSnapshot:
        """Create a root plan snapshot or verify the exact snapshot from an earlier attempt."""
        budget_id = self._identity("plan-budget", plan_id)
        task_id = self._task_identity("plan", plan_id)
        try:
            snapshot = self.store.create_snapshot(
                budget_id=budget_id, task_id=task_id, limits=limits,
                policy_version=policy_version,
            )
        except ExecutionBudgetStoreError as exc:
            if str(exc) != "budget_or_task_already_snapshotted":
                raise AgentBudgetAllocationError("plan_budget_creation_failed") from exc
            try:
                snapshot = self.store.get_snapshot(budget_id)
            except (KeyError, ExecutionBudgetStoreError) as lookup_exc:
                raise AgentBudgetAllocationError("plan_budget_retry_could_not_be_verified") from lookup_exc
        return self._verify(
            snapshot, budget_id=budget_id, task_id=task_id,
            parent_budget_id=None, policy_version=policy_version, limits=limits,
        )

    def ensure_subtask_budget(self, *, plan_id: str, subtask_id: str,
                              parent_budget_id: str, limits: Mapping[str, int],
                              policy_version: str) -> ExecutionBudgetSnapshot:
        """Allocate one stable child snapshot; safe to retry after process restart."""
        budget_id = self._identity("subtask-budget", plan_id, subtask_id)
        task_id = self._task_identity("subtask", plan_id, subtask_id)
        allocation_key = self._identity("subtask-allocation", plan_id, subtask_id)
        try:
            snapshot = self.store.allocate_child_snapshot(
                parent_budget_id=parent_budget_id,
                child_budget_id=budget_id,
                task_id=task_id,
                limits=limits,
                policy_version=policy_version,
                allocation_key=allocation_key,
            )
        except ExecutionBudgetStoreError as exc:
            raise AgentBudgetAllocationError("subtask_budget_allocation_failed") from exc
        return self._verify(
            snapshot, budget_id=budget_id, task_id=task_id,
            parent_budget_id=parent_budget_id,
            policy_version=policy_version, limits=limits,
        )
