"""Coordinator for separately approved self-upgrade validation in a disposable copy.

Safe workspace staging requires POSIX descriptor-relative open support and O_NOFOLLOW;
unsupported platforms fail closed. This coordinator is intended for the Linux rootless
worker path, not direct Windows-host execution.

Important: a temporary directory is not an OS security sandbox. Running proposed
Python tests can execute arbitrary code. Use a dedicated low-privilege worker with
no production credentials and restricted network access for untrusted proposals.
This coordinator never promotes changes back to the source workspace.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import re
import stat
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
_SENSITIVE_FILE = re.compile(r"(secret|credential|password|token|private.?key|id_rsa|id_ed25519|\.pem$|\.key$)", re.I)


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
        allow_host_test_execution: bool = False,
        isolated_runner_factory: Callable[[Path], object] | None = None,
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
        if isinstance(test_timeout_seconds, bool) or not isinstance(test_timeout_seconds, int) or not 1 <= test_timeout_seconds <= 120:
            raise ValueError("test_timeout_seconds must be an integer from 1 to 120.")
        self.test_timeout_seconds = test_timeout_seconds
        if type(allow_host_test_execution) is not bool:
            raise ValueError("allow_host_test_execution must be a boolean.")
        self.allow_host_test_execution = allow_host_test_execution
        if isolated_runner_factory is not None and not callable(isolated_runner_factory):
            raise TypeError("isolated_runner_factory must be callable when provided.")
        self.isolated_runner_factory = isolated_runner_factory
        self.proposal_builder = SelfUpgradeProposalBuilder(self.allowed_paths)

    @staticmethod
    def _open_component(name: str, flags: int, *, dir_fd: int) -> int:
        """Open one path component relative to a verified directory descriptor."""
        return os.open(name, flags, dir_fd=dir_fd)

    def _open_workspace_file(self, relative: Path, root_fd: int) -> tuple[int, os.stat_result]:
        """Open a regular workspace file without following swapped symlinks.

        Parent components and the leaf are opened relative to descriptors with
        O_NOFOLLOW. Platforms without these primitives fail closed.
        """
        nofollow = getattr(os, "O_NOFOLLOW", None)
        directory_flag = getattr(os, "O_DIRECTORY", None)
        if nofollow is None or directory_flag is None or os.open not in getattr(os, "supports_dir_fd", set()):
            raise WorkspaceChangeError("safe descriptor-based workspace staging is unsupported on this platform.")
        parts = relative.parts
        if not parts or any(part in ("", ".", "..") for part in parts):
            raise WorkspaceChangeError("workspace staging denied: unsafe relative path.")
        parent_fd = root_fd
        owned_parent_fd = None
        file_fd = None
        try:
            for component in parts[:-1]:
                next_fd = self._open_component(
                    component,
                    os.O_RDONLY | directory_flag | nofollow | getattr(os, "O_CLOEXEC", 0),
                    dir_fd=parent_fd,
                )
                if owned_parent_fd is not None:
                    os.close(owned_parent_fd)
                owned_parent_fd = next_fd
                parent_fd = next_fd
            leaf = parts[-1]
            before = os.stat(leaf, dir_fd=parent_fd, follow_symlinks=False)
            if not stat.S_ISREG(before.st_mode):
                raise WorkspaceChangeError("workspace staging denied: only regular files may be copied.")
            file_fd = self._open_component(
                leaf,
                os.O_RDONLY | nofollow | getattr(os, "O_BINARY", 0) | getattr(os, "O_CLOEXEC", 0),
                dir_fd=parent_fd,
            )
            opened = os.fstat(file_fd)
            after = os.stat(leaf, dir_fd=parent_fd, follow_symlinks=False)
            identity = (opened.st_dev, opened.st_ino)
            if (not stat.S_ISREG(opened.st_mode) or not stat.S_ISREG(after.st_mode)
                    or identity != (before.st_dev, before.st_ino)
                    or identity != (after.st_dev, after.st_ino)):
                raise WorkspaceChangeError("workspace file changed during safe staging open.")
            result_fd, file_fd = file_fd, None
            return result_fd, opened
        except WorkspaceChangeError:
            raise
        except (OSError, ValueError, TypeError) as exc:
            raise WorkspaceChangeError("workspace staging denied: unable to safely open a regular file.") from exc
        finally:
            if file_fd is not None:
                os.close(file_fd)
            if owned_parent_fd is not None:
                os.close(owned_parent_fd)

    def _copy_workspace(self, destination: Path) -> None:
        file_count = 0
        byte_count = 0
        nofollow = getattr(os, "O_NOFOLLOW", None)
        directory_flag = getattr(os, "O_DIRECTORY", None)
        if nofollow is None or directory_flag is None or os.open not in getattr(os, "supports_dir_fd", set()):
            raise WorkspaceChangeError("safe descriptor-based workspace staging is unsupported on this platform.")
        try:
            root_fd = os.open(self.root, os.O_RDONLY | directory_flag | nofollow | getattr(os, "O_CLOEXEC", 0))
        except OSError as exc:
            raise WorkspaceChangeError("workspace staging denied: unable to open workspace root safely.") from exc
        try:
            for current, dirs, files in os.walk(self.root, topdown=True, followlinks=False):
                current_path = Path(current)
                dirs[:] = [
                    name for name in dirs
                    if name not in _BLOCKED_DIRS
                    and not name.lower().startswith(".env")
                    and not (current_path / name).is_symlink()
                ]
                relative_dir = current_path.relative_to(self.root)
                target_dir = destination / relative_dir
                target_dir.mkdir(parents=True, exist_ok=True)
                for name in files:
                    if name.lower().startswith(".env") or _SENSITIVE_FILE.search(name):
                        continue
                    relative = relative_dir / name
                    try:
                        file_fd, _metadata = self._open_workspace_file(relative, root_fd)
                    except WorkspaceChangeError:
                        # A raced symlink/non-regular entry is excluded. Other
                        # failures deny staging; never fall back to pathname copy.
                        try:
                            if len(relative.parts) == 1:
                                entry = os.stat(name, dir_fd=root_fd, follow_symlinks=False)
                            else:
                                entry = None
                        except OSError:
                            entry = None
                        if entry is not None and not stat.S_ISREG(entry.st_mode):
                            continue
                        raise
                    file_count += 1
                    if file_count > _MAX_COPY_FILES:
                        os.close(file_fd)
                        raise WorkspaceChangeError("workspace copy exceeded configured safety bounds.")
                    target = target_dir / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    try:
                        with os.fdopen(file_fd, "rb") as source_stream, target.open("wb") as target_stream:
                            while True:
                                chunk = source_stream.read(1024 * 1024)
                                if not chunk:
                                    break
                                byte_count += len(chunk)
                                if byte_count > _MAX_COPY_BYTES:
                                    raise WorkspaceChangeError("workspace copy exceeded configured safety bounds.")
                                target_stream.write(chunk)
                    except WorkspaceChangeError:
                        target.unlink(missing_ok=True)
                        raise
                    except OSError as exc:
                        target.unlink(missing_ok=True)
                        raise WorkspaceChangeError("workspace staging denied while copying a verified file.") from exc
        finally:
            os.close(root_fd)

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

        if not self.allow_host_test_execution and self.isolated_runner_factory is None:
            raise ToolAuthorizationError(
                "host test execution is disabled; configure a dedicated isolated runner factory before validating proposals."
            )

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

            if self.allow_host_test_execution:
                runner = ApprovedTestRunner(
                    staged_root,
                    trusted_principal=self.trusted_principal,
                    approve_run=self.approve_test,
                    audit=self.audit,
                    allowed_test_modules=self.allowed_test_modules,
                    timeout_seconds=self.test_timeout_seconds,
                )
            else:
                # The factory must bind the isolated worker to this exact staged copy.
                runner = self.isolated_runner_factory(staged_root)
                if not callable(getattr(runner, "run", None)):
                    raise ToolAuthorizationError("isolated runner factory returned an invalid runner.")
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
