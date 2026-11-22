"""Immutable, task-scoped artifact bytes stored inside private task workspaces.

The store is deliberately additive: callers persist the returned opaque reference
through TaskRuntime.record_artifact after the bytes have been committed.
"""
from __future__ import annotations

import hashlib
import os
import re
import stat
import uuid
from typing import Callable

from core.task_workspace import TaskWorkspaceResolver


_REFERENCE = re.compile(r"^yuki-artifact://sha256/([0-9a-f]{64})$")
_MAX_ARTIFACT_BYTES = 16 * 1024 * 1024


class TaskArtifactError(RuntimeError):
    """An artifact operation failed or violated its storage boundary."""


class TaskArtifactStore:
    """Store immutable artifact bytes in the authenticated task's private workspace.

    Authorization is checked for every read and write. References are content
    digests, but resolution is additionally scoped to the supplied task identity,
    so another task cannot use a digest as a cross-task locator.
    """

    def __init__(
        self,
        workspace_resolver: TaskWorkspaceResolver,
        *,
        authorize: Callable[[str, str, str], bool],
        max_artifact_bytes: int = _MAX_ARTIFACT_BYTES,
    ) -> None:
        if not isinstance(workspace_resolver, TaskWorkspaceResolver):
            raise TypeError("workspace_resolver must be a TaskWorkspaceResolver")
        if not callable(authorize):
            raise TypeError("authorize must be callable")
        if type(max_artifact_bytes) is not int or not 1 <= max_artifact_bytes <= _MAX_ARTIFACT_BYTES:
            raise ValueError("max_artifact_bytes must be between 1 byte and 16 MiB")
        self.workspace_resolver = workspace_resolver
        self.authorize = authorize
        self.max_artifact_bytes = max_artifact_bytes

    @staticmethod
    def _identity(task: object) -> tuple[str, str]:
        owner = getattr(task, "owner_id", None)
        task_id = getattr(task, "task_id", None) or getattr(task, "run_id", None)
        if (
            type(owner) is not str or not owner.strip() or len(owner) > 200 or "\x00" in owner
            or type(task_id) is not str or not task_id.strip() or len(task_id) > 200 or "\x00" in task_id
        ):
            raise TaskArtifactError("persisted task owner and identity are required")
        return owner.strip(), task_id.strip()

    def _check(self, actor_id: str, action: str, task_id: str) -> None:
        if type(actor_id) is not str or not actor_id.strip() or len(actor_id) > 200 or "\x00" in actor_id:
            raise PermissionError("authenticated artifact actor is required")
        try:
            allowed = self.authorize(actor_id.strip(), action, task_id) is True
        except Exception:
            allowed = False
        if not allowed:
            raise PermissionError("artifact operation is not authorized")

    @staticmethod
    def _open_directory(path: str) -> int:
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
        try:
            fd = os.open(path, flags)
        except OSError as exc:
            raise TaskArtifactError("task artifact directory could not be safely opened") from exc
        info = os.fstat(fd)
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
            os.close(fd)
            raise TaskArtifactError("task artifact directory is not private and service-owned")
        return fd

    def _artifact_directory(self, task: object) -> int:
        workspace = self.workspace_resolver(task)
        workspace_fd = self._open_directory(workspace)
        try:
            try:
                os.mkdir("artifacts", mode=0o700, dir_fd=workspace_fd)
            except FileExistsError:
                pass
            flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
            try:
                artifact_fd = os.open("artifacts", flags, dir_fd=workspace_fd)
            except OSError as exc:
                raise TaskArtifactError("artifact directory could not be safely opened") from exc
            info = os.fstat(artifact_fd)
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
                os.close(artifact_fd)
                raise TaskArtifactError("artifact directory is not private and service-owned")
            return artifact_fd
        finally:
            os.close(workspace_fd)

    def put(self, *, actor_id: str, task: object, content: bytes) -> str:
        _, task_id = self._identity(task)
        self._check(actor_id, "task:artifact:write", task_id)
        if type(content) is not bytes or not content or len(content) > self.max_artifact_bytes:
            raise ValueError("artifact content must be non-empty bytes within the configured size limit")
        digest = hashlib.sha256(content).hexdigest()
        directory_fd = self._artifact_directory(task)
        temporary_name = f".{digest}.{uuid.uuid4().hex}.tmp"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
        try:
            fd = os.open(temporary_name, flags, 0o600, dir_fd=directory_fd)
            try:
                view = memoryview(content)
                while view:
                    written = os.write(fd, view)
                    if written <= 0:
                        raise OSError("short artifact write")
                    view = view[written:]
                os.fsync(fd)
            finally:
                os.close(fd)
            try:
                # Linking publishes a fully written inode without replacing an
                # existing immutable digest or exposing a partially written file.
                os.link(
                    temporary_name, digest, src_dir_fd=directory_fd,
                    dst_dir_fd=directory_fd, follow_symlinks=False,
                )
                os.fsync(directory_fd)
            except FileExistsError:
                existing = self._read_digest(directory_fd, digest)
                if existing != content:
                    raise TaskArtifactError("existing artifact failed content integrity verification")
            finally:
                try:
                    os.unlink(temporary_name, dir_fd=directory_fd)
                except FileNotFoundError:
                    pass
        except OSError as exc:
            raise TaskArtifactError("artifact could not be committed safely") from exc
        finally:
            try:
                os.unlink(temporary_name, dir_fd=directory_fd)
            except FileNotFoundError:
                pass
            os.close(directory_fd)
        return f"yuki-artifact://sha256/{digest}"


    def get(self, *, actor_id: str, task: object, reference: str) -> bytes:
        _, task_id = self._identity(task)
        self._check(actor_id, "task:artifact:read", task_id)
        if type(reference) is not str:
            raise ValueError("artifact reference must be text")
        match = _REFERENCE.fullmatch(reference)
        if match is None:
            raise ValueError("artifact reference is not a supported opaque Yuki reference")
        directory_fd = self._artifact_directory(task)
        try:
            return self._read_digest(directory_fd, match.group(1))
        finally:
            os.close(directory_fd)

    def _read_digest(self, directory_fd: int, digest: str) -> bytes:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
        try:
            fd = os.open(digest, flags, dir_fd=directory_fd)
        except OSError as exc:
            raise TaskArtifactError("artifact is missing or cannot be safely opened") from exc
        try:
            info = os.fstat(fd)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) != 0o600
            ):
                raise TaskArtifactError("artifact file is not private and service-owned")
            if info.st_size < 1 or info.st_size > self.max_artifact_bytes:
                raise TaskArtifactError("artifact file size is outside the permitted range")
            chunks = []
            remaining = self.max_artifact_bytes + 1
            while remaining:
                chunk = os.read(fd, min(65536, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            data = b"".join(chunks)
            if (
                len(data) != info.st_size
                or hashlib.sha256(data).hexdigest() != digest
            ):
                raise TaskArtifactError("artifact content digest verification failed")
            return data
        finally:
            os.close(fd)
