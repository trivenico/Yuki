"""Coordinator for separately approved self-upgrade validation in a disposable copy.

Important: a temporary directory is not an OS security sandbox. Running proposed
Python tests can execute arbitrary code. Use a dedicated low-privilege worker with
no production credentials and restricted network access for untrusted proposals.
This coordinator never promotes changes back to the source workspace.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil
import tempfile
from typing import Callable, Sequence

from automation.approved_workspace_changes import (
    ApprovedTestRunner,
    ApprovedWorkspaceChanges,
    FileChange,
    WorkspaceChangeError,
)
from automation.authorized_read_tools import ToolPrincipal, ToolAuthorizationError, ToolInputError
from automation.self_upgrade_pipeline import SelfUpgradeProposalBuilder, UpgradeProposal

_BLOCKED_DIRS = frozenset({
    ".git", ".venv", "venv", "node_modules", "__pycache__", ".ssh",
    ".aws", ".azure", ".config", "dist", "build",
})
_MAX_COPY_FILES = 3000
_MAX_COPY_BYTES = 100 * 1024 * 1024


class IsolatedUpgradeCoordinator:
    """Apply approved changes to a disposable copy and run approved test targets."""

    def __init__(
        self,
        workspace: str | Path,
        *,
        trusted_principal: Callable[[ToolPrincipal], bool],
        approve_change: Callable[[str, str, str], bool],
        approve_test: Callable[[str, str, str], bool],
        audit: Callable[[dict], None],
        allowed_paths: Sequence[str],
        allowed_test_modules: Sequence[str],
        test_timeout_seconds: int = 60,
    ) -> None:
        self.root = Path(workspace).expanduser().resolve()
        if not self.root.is_dir():
            raise ValueError("workspace must be an existing directory.")
        if not all(callable(fn) for fn in (trusted_principal, approve_change, approve_test, audit)):
            raise TypeError("trusted principal, approvals and audit callbacks are required.")
        if not isinstance(allowed_paths, (tuple, list)) or not allowed_paths:
            raise ValueError("explicit allowed_paths are required.")
        if not isinstance(allowed_test_modules, (tuple, list)) or not allowed_test_modules:
            raise ValueError("explicit allowed_test_modules are required.")
        self.trusted_principal = trusted_principal
        self.approve_change = approve_change
        self.approve_test = approve_test
        self.audit = audit
        self.allowed_paths = tuple(allowed_paths)
        self.allowed_test_modules = tuple(allowed_test_modules)
        self.test_timeout_seconds = test_timeout_seconds
        self.proposal_builder = SelfUpgradeProposalBuilder(self.allowed_paths)

    def _copy_workspace(self, destination: Path) -> None:
        file_count = 0
        byte_count = 0
        for current, dirs, files in os.walk(self.root, topdown=True, followlinks=False):
            current_path = Path(current)
            dirs[:] = [
                name for name in dirs
                if name not in _BLOCKED_DIRS
                and not name.lower().startswith(".env")
                and not (current_path / name).is_symlink()
            ]
            relative = current_path.relative_to(self.root)
            target_dir = destination / relative
            target_dir.mkdir(parents=True, exist_ok=True)
            for name in files:
                source = current_path / name
                if source.is_symlink() or name.lower().startswith(".env"):
                    continue
                try:
                    if not source.is_file():
                        continue
                    size = source.stat().st_size
                except OSError:
                    continue
                file_count += 1
                byte_count += size
                if file_count > _MAX_COPY_FILES or byte_count > _MAX_COPY_BYTES:
                    raise WorkspaceChangeError("workspace copy exceeded configured safety bounds.")
                target = target_dir / name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)

    def validate(
        self,
        *,
        principal: ToolPrincipal,
        changes: Sequence[FileChange],
    ) -> dict:
        if not isinstance(principal, ToolPrincipal):
            raise ToolAuthorizationError("trusted authenticated principal required.")
        try:
            if self.trusted_principal(principal) is not True:
                raise ToolAuthorizationError("principal validation failed closed.")
        except ToolAuthorizationError:
            raise
        except Exception as exc:
            raise ToolAuthorizationError("principal validation failed closed.") from exc
        if "workspace:write" not in principal.permissions or "tests:execute" not in principal.permissions:
            raise ToolAuthorizationError("workspace:write and tests:execute permissions are required.")

        proposal: UpgradeProposal = self.proposal_builder.build(changes)
        with tempfile.TemporaryDirectory(prefix="yuki-upgrade-validation-") as temp_name:
            staged_root = Path(temp_name) / "workspace"
            staged_root.mkdir()
            self._copy_workspace(staged_root)
            change_service = ApprovedWorkspaceChanges(
                staged_root,
                trusted_principal=self.trusted_principal,
                approve_change=self.approve_change,
                audit=self.audit,
                allowed_paths=self.allowed_paths,
            )
            applied = change_service.apply(principal=principal, changes=changes)

            runner = ApprovedTestRunner(
                staged_root,
                trusted_principal=self.trusted_principal,
                approve_run=self.approve_test,
                audit=self.audit,
                allowed_test_modules=self.allowed_test_modules,
                timeout_seconds=self.test_timeout_seconds,
            )
            test_results = []
            for module in self.allowed_test_modules:
                result = runner.run(principal=principal, test_module=module)
                test_results.append({
                    "target": result.target,
                    "exit_code": result.exit_code,
                    "timed_out": result.timed_out,
                    "duration_seconds": result.duration_seconds,
                    "stdout": result.stdout[-16000:],
                    "stderr": result.stderr[-16000:],
                })
                if result.timed_out or result.exit_code != 0:
                    break

            passed = (
                len(test_results) == len(self.allowed_test_modules)
                and all(item["exit_code"] == 0 and not item["timed_out"] for item in test_results)
            )
            event = {
                "action": "self_upgrade.validation",
                "outcome": "passed" if passed else "failed",
                "proposal_id": proposal.proposal_id,
                "content_digest": proposal.content_digest,
                "change_digest": applied.content_digest,
                "paths": list(proposal.changed_paths),
                "test_targets": [item["target"] for item in test_results],
                "tests_passed": passed,
                "promotion_allowed": False,
            }
            try:
                self.audit(event)
            except Exception as exc:
                raise RuntimeError("audit unavailable; upgrade validation denied.") from exc
            return event
