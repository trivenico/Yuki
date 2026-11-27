"""Race-resistant POSIX workspace file access rooted at an open directory FD.

The resolver's canonical resource is revalidated, then every path component is
opened relative to an already-open directory descriptor with O_NOFOLLOW.
Reads never follow a final symlink. Writes use a same-directory temporary file,
fsync, and descriptor-relative rename. This is intentionally POSIX-only.
"""
from __future__ import annotations

import os
import secrets
import stat
from core.workspace_resource_scope import (
    WorkspaceResource,
    WorkspaceResourceScopeResolver,
)


class WorkspaceFileAccessError(PermissionError):
    """A workspace operation was rejected or could not be completed safely."""


class SecureWorkspaceFiles:
    def __init__(
        self,
        resolver: WorkspaceResourceScopeResolver,
        *,
        max_read_bytes: int = 8 * 1024 * 1024,
        max_write_bytes: int = 8 * 1024 * 1024,
        file_mode: int = 0o600,
    ):
        if os.name != "posix" or not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
            raise NotImplementedError("secure workspace file access requires POSIX O_NOFOLLOW and O_DIRECTORY")
        if not isinstance(resolver, WorkspaceResourceScopeResolver):
            raise TypeError("resolver must be WorkspaceResourceScopeResolver")
        for name, value in (("max_read_bytes", max_read_bytes), ("max_write_bytes", max_write_bytes)):
            if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 64 * 1024 * 1024:
                raise ValueError(name + " must be between 1 byte and 64 MiB")
        if isinstance(file_mode, bool) or not isinstance(file_mode, int) or file_mode & ~0o600:
            raise ValueError("file_mode may contain only owner read/write permissions")
        self._resolver = resolver
        self._max_read = max_read_bytes
        self._max_write = max_write_bytes
        self._file_mode = file_mode
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
        self._root_fd = os.open(str(resolver.root), flags)
        try:
            self._assert_root_identity()
        except Exception:
            os.close(self._root_fd)
            self._root_fd = None
            raise

    def close(self) -> None:
        if self._root_fd is not None:
            os.close(self._root_fd)
            self._root_fd = None

    def __enter__(self):
        if self._root_fd is None:
            raise WorkspaceFileAccessError("workspace handle is closed")
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()

    def _require_open(self):
        if self._root_fd is None:
            raise WorkspaceFileAccessError("workspace handle is closed")

    def _assert_root_identity(self):
        self._require_open()
        try:
            opened = os.fstat(self._root_fd)
            named = os.stat(self._resolver.root, follow_symlinks=False)
        except OSError as exc:
            raise WorkspaceFileAccessError("workspace root identity cannot be verified") from exc
        if (
            not stat.S_ISDIR(opened.st_mode)
            or not stat.S_ISDIR(named.st_mode)
            or (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino)
        ):
            raise WorkspaceFileAccessError("workspace root changed after it was opened")

    def _verified_relative(self, resource: WorkspaceResource) -> tuple[str, ...]:
        self._assert_root_identity()
        if not isinstance(resource, WorkspaceResource):
            raise TypeError("resource must be a WorkspaceResource")
        if resource.workspace_id != self._resolver.workspace_id:
            raise WorkspaceFileAccessError("resource belongs to another workspace")
        try:
            verified = self._resolver.resolve(resource.relative_path)
        except (OSError, ValueError) as exc:
            raise WorkspaceFileAccessError("resource is no longer within workspace") from exc
        if (
            verified.scope_id != resource.scope_id
            or verified.canonical_path != resource.canonical_path
            or verified.relative_path != resource.relative_path
        ):
            raise WorkspaceFileAccessError("resource scope changed after authorization")
        parts = tuple(resource.relative_path.split("/"))
        if not parts or any(part in ("", ".", "..") for part in parts):
            raise WorkspaceFileAccessError("invalid resource path")
        return parts

    def _open_parent(self, parts: tuple[str, ...]) -> tuple[int, str]:
        current = os.dup(self._root_fd)
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
        try:
            for component in parts[:-1]:
                next_fd = os.open(component, flags, dir_fd=current)
                os.close(current)
                current = next_fd
            return current, parts[-1]
        except Exception:
            os.close(current)
            raise

    def read_bytes(self, resource: WorkspaceResource) -> bytes:
        parts = self._verified_relative(resource)
        parent_fd, leaf = self._open_parent(parts)
        flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_CLOEXEC", 0)
        fd = None
        try:
            fd = os.open(leaf, flags, dir_fd=parent_fd)
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode):
                raise WorkspaceFileAccessError("only regular files may be read")
            if info.st_size > self._max_read:
                raise WorkspaceFileAccessError("file exceeds configured read limit")
            chunks = []
            total = 0
            while True:
                chunk = os.read(fd, min(65536, self._max_read + 1 - total))
                if not chunk:
                    break
                total += len(chunk)
                if total > self._max_read:
                    raise WorkspaceFileAccessError("file exceeds configured read limit")
                chunks.append(chunk)
            return b"".join(chunks)
        except OSError as exc:
            raise WorkspaceFileAccessError("safe workspace read failed") from exc
        finally:
            if fd is not None:
                os.close(fd)
            os.close(parent_fd)

    def write_bytes(self, resource: WorkspaceResource, data: bytes) -> None:
        if not isinstance(data, bytes):
            raise TypeError("data must be bytes")
        if len(data) > self._max_write:
            raise WorkspaceFileAccessError("content exceeds configured write limit")
        parts = self._verified_relative(resource)
        parent_fd, leaf = self._open_parent(parts)
        temp_name = ".yuki-" + secrets.token_hex(16) + ".tmp"
        temp_fd = None
        try:
            try:
                existing_fd = os.open(
                    leaf,
                    os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_CLOEXEC", 0),
                    dir_fd=parent_fd,
                )
            except FileNotFoundError:
                existing_fd = None
            if existing_fd is not None:
                try:
                    if not stat.S_ISREG(os.fstat(existing_fd).st_mode):
                        raise WorkspaceFileAccessError("only regular files may be replaced")
                finally:
                    os.close(existing_fd)

            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
            temp_fd = os.open(temp_name, flags, self._file_mode, dir_fd=parent_fd)
            os.fchmod(temp_fd, self._file_mode)
            view = memoryview(data)
            while view:
                written = os.write(temp_fd, view)
                if written <= 0:
                    raise OSError("short write")
                view = view[written:]
            os.fsync(temp_fd)
            os.close(temp_fd)
            temp_fd = None
            # renameat replaces a final symlink itself; it never follows it.
            # Parent directory FD remains anchored even if its name is swapped.
            os.rename(temp_name, leaf, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
            os.fsync(parent_fd)
        except WorkspaceFileAccessError:
            raise
        except OSError as exc:
            raise WorkspaceFileAccessError("safe workspace write failed") from exc
        finally:
            if temp_fd is not None:
                os.close(temp_fd)
            try:
                os.unlink(temp_name, dir_fd=parent_fd)
            except FileNotFoundError:
                pass
            os.close(parent_fd)
