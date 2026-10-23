"""Explicit end-to-end engineering workflow orchestration.

The orchestrator sequences read-only inspection, a supplied change proposal, digest-
bound approved edits, and an explicitly approved allow-listed test run. It never asks
the model to execute tools or invent commands, and it stops on the first failure.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Sequence

from automation.approved_workspace_changes import (
    ApprovedTestRunner,
    ApprovedWorkspaceChanges,
    ChangeResult,
    FileChange,
    TestRunResult,
)
from automation.authorized_read_tools import (
    AuthorizedReadOnlyTools,
    ToolPrincipal,
)
from automation.specialist_read_tool_adapter import build_read_tool_specialist_handler


@dataclass(frozen=True)
class EngineeringWorkflowResult:
    workflow_id: str
    inspection_report: str
    change_result: ChangeResult
    test_result: TestRunResult
    outcome: str


class EngineeringWorkflow:
    """Compose existing controls without weakening their individual authorization."""

    def __init__(
        self,
        *,
        provider,
        read_tools: AuthorizedReadOnlyTools,
        principal_resolver,
        workspace_changes: ApprovedWorkspaceChanges,
        test_runner: ApprovedTestRunner,
        audit,
        allowed_inspection_tools: tuple[str, ...] = (
            "workspace.list", "workspace.read", "workspace.search"
        ),
    ):
        if not callable(audit):
            raise TypeError("audit callback is required.")
        self.inspect = build_read_tool_specialist_handler(
            provider, read_tools, principal_resolver=principal_resolver,
            allowed_tool_names=allowed_inspection_tools,
        )
        self.workspace_changes = workspace_changes
        self.test_runner = test_runner
        self.audit = audit

    def run(
        self,
        *,
        principal: ToolPrincipal,
        task: str,
        inspection_calls: list[dict],
        proposed_changes: Sequence[FileChange],
        test_module: str,
    ) -> EngineeringWorkflowResult:
        if not isinstance(principal, ToolPrincipal):
            raise PermissionError("authenticated principal required.")
        if not isinstance(task, str) or not task.strip() or len(task) > 2000:
            raise ValueError("task must be bounded non-empty text.")
        if not isinstance(test_module, str) or not test_module:
            raise ValueError("an explicit allow-listed test module is required.")
        change_facts = []
        for change in proposed_changes:
            if not isinstance(change, FileChange):
                raise ValueError("proposed_changes must contain FileChange objects.")
            change_facts.append({
                "path": change.path,
                "expected_sha256": change.expected_sha256,
                "proposed_sha256": hashlib.sha256(change.content.encode("utf-8")).hexdigest(),
            })
        workflow_digest = hashlib.sha256(json.dumps({
            "actor": principal.actor_id, "task": task,
            "inspection_calls": inspection_calls, "changes": change_facts,
            "test_module": test_module,
        }, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
        workflow_id = "eng_" + workflow_digest[:24]
        self._record(principal.actor_id, workflow_id, "started", workflow_digest)

        report = self.inspect(json.dumps({
            "task": task,
            "tool_calls": inspection_calls,
        }, ensure_ascii=False))
        self._record(principal.actor_id, workflow_id, "inspection_completed", workflow_digest)

        # Existing services independently enforce permissions, approvals, path policy,
        # source digests and audit preflight. Do not continue to tests after an edit error.
        changed = self.workspace_changes.apply(
            principal=principal, changes=proposed_changes,
        )
        self._record(principal.actor_id, workflow_id, "changes_applied", workflow_digest)

        tests = self.test_runner.run(principal=principal, test_module=test_module)
        outcome = "succeeded" if tests.exit_code == 0 and not tests.timed_out else "tests_failed"
        self._record(principal.actor_id, workflow_id, outcome, workflow_digest)
        return EngineeringWorkflowResult(workflow_id, report, changed, tests, outcome)

    def _record(self, actor: str, workflow_id: str, stage: str, digest: str) -> None:
        try:
            self.audit({
                "actor_id": actor[:200],
                "workflow_id": workflow_id,
                "stage": stage,
                "workflow_digest": digest,
            })
        except Exception as exc:
            raise RuntimeError("engineering workflow audit unavailable; stopping.") from exc
