"""Private, account-scoped task workspace allocation.

The resolver accepts only persisted task identity fields. It never accepts a
workspace path from a task definition, model output, or user request.
"""
from __future__ import annotations

import hashlib
import os
import stat
from typing import Any


class WorkspaceSecurityError(RuntimeError):
    """Raised when the configured workspace boundary cannot be trusted."""


class TaskWorkspaceResolver:
    """Create and resolve private per-owner/per-task directories.

    The root must be pre-provisioned, absolute, non-symlinked, owned by the
    service UID, and not writable by group or other users. Child directory
    creation and opening use directory file descriptors and O_NOFOLLOW so an
    attacker cannot substitute symlinks between path checks and opens.
    """

    def __init__(self, root: str | os.PathLike[str]) -> None:
        if not isinstance(root, (str, os.PathLike)):
            raise TypeError("workspace root must be a filesystem path.")
        raw = os.fspath(root)
        if not isinstance(raw, str) or not raw or not os.path.isabs(raw):
            raise ValueError("workspace root must be a non-empty absolute path.")
        normalized = os.path.abspath(raw)
        if os.path.realpath(normalized) != normalized:
            raise WorkspaceSecurityError("workspace root must not contain symlink components.")
        try:
            info = os.stat(normalized, follow_symlinks=False)
        except OSError as exc:
            raise WorkspaceSecurityError("workspace root is unavailable.") from exc
        if not stat.S_ISDIR(info.st_mode):
            raise WorkspaceSecurityError("workspace root must be a directory.")
        if info.st_uid != os.geteuid() or info.st_mode & 0o022:
            raise WorkspaceSecurityError(
                "workspace root must be service-owned and not group/world writable."
            )
        parent_info = os.stat(os.path.dirname(normalized), follow_symlinks=False)
        if parent_info.st_mode & 0o022 and not parent_info.st_mode & stat.S_ISVTX:
            raise WorkspaceSecurityError(
                "workspace root parent must not be group/world writable unless sticky."
            )
        self.root = normalized

    @staticmethod
    def _identity(value: Any, field: str) -> str:
        if (
            not isinstance(value, str) or not value.strip()
            or len(value) > 200 or chr(0) in value
        ):
            raise WorkspaceSecurityError(f"task {field} is missing or invalid.")
        return value.strip()

    @staticmethod
    def _directory_flags() -> int:
        required = ("O_DIRECTORY", "O_NOFOLLOW")
        if any(not hasattr(os, name) for name in required) or os.open not in os.supports_dir_fd:
            raise WorkspaceSecurityError(
                "platform lacks safe directory-relative workspace operations."
            )
        return (
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
            | getattr(os, "O_CLOEXEC", 0)
        )

    @staticmethod
    def _open_private_child(parent_fd: int, name: str) -> int:
        try:
            try:
                os.mkdir(name, mode=0o700, dir_fd=parent_fd)
            except FileExistsError:
                pass
            child_fd = os.open(
                name, TaskWorkspaceResolver._directory_flags(),
                dir_fd=parent_fd,
            )
        except OSError as exc:
            raise WorkspaceSecurityError(
                "workspace directory could not be safely opened."
            ) from exc
        info = os.fstat(child_fd)
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o700
        ):
            os.close(child_fd)
            raise WorkspaceSecurityError(
                "workspace directory is not private and service-owned."
            )
        return child_fd

    def __call__(self, task: object) -> str:
        owner_id = self._identity(getattr(task, "owner_id", None), "owner_id")
        task_id = self._identity(getattr(task, "task_id", None), "task_id")
        owner_component = hashlib.sha256(owner_id.encode("utf-8")).hexdigest()
        task_component = hashlib.sha256(task_id.encode("utf-8")).hexdigest()
        flags = self._directory_flags()
        try:
            root_fd = os.open(self.root, flags)
        except OSError as exc:
            raise WorkspaceSecurityError("workspace root could not be safely opened.") from exc
        owner_fd = None
        task_fd = None
        try:
            root_info = os.fstat(root_fd)
            if (
                not stat.S_ISDIR(root_info.st_mode)
                or root_info.st_uid != os.geteuid()
                or root_info.st_mode & 0o022
            ):
                raise WorkspaceSecurityError("workspace root trust checks failed.")
            owner_fd = self._open_private_child(root_fd, owner_component)
            task_fd = self._open_private_child(owner_fd, task_component)
            return os.path.join(self.root, owner_component, task_component)
        finally:
            if task_fd is not None:
                os.close(task_fd)
            if owner_fd is not None:
                os.close(owner_fd)
            os.close(root_fd)
