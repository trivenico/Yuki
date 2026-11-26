"""Descriptor-based, allowlist-only POSIX workspace snapshot staging.

This is a staging primitive, not sandbox authorization or runtime attestation.
Callers must provide an explicit reviewed list of relative regular-file paths.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import stat
import unicodedata
from typing import Iterable

from core.private_profile import PrivateProfileError, ensure_private_profile_directory


class WorkspaceStagingError(RuntimeError):
    """A workspace snapshot could not be created safely."""


@dataclass(frozen=True)
class WorkspaceSnapshot:
    attempt_id: str
    path: str
    manifest_sha256: str
    file_count: int
    total_bytes: int
    manifest_json: str


@dataclass(frozen=True)
class _Limits:
    max_files: int = 10_000
    max_file_bytes: int = 10 * 1024 * 1024
    max_total_bytes: int = 100 * 1024 * 1024
    max_path_bytes: int = 1024
    max_depth: int = 32
    chunk_bytes: int = 1024 * 1024


def _require_posix_fd_support() -> None:
    if os.name != "posix" or not hasattr(os, "O_NOFOLLOW") or not hasattr(os, "O_DIRECTORY"):
        raise WorkspaceStagingError("workspace_staging_posix_descriptor_support_required")
    if os.open not in getattr(os, "supports_dir_fd", set()):
        raise WorkspaceStagingError("workspace_staging_dir_fd_unavailable")
    if os.mkdir not in getattr(os, "supports_dir_fd", set()):
        raise WorkspaceStagingError("workspace_staging_mkdirat_unavailable")
    if os.stat not in getattr(os, "supports_dir_fd", set()):
        raise WorkspaceStagingError("workspace_staging_fstatat_unavailable")


def _open_absolute_directory(path: str | Path) -> tuple[int, os.stat_result]:
    target = Path(os.path.abspath(Path(path).expanduser()))
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    fd = os.open(target.anchor, flags)
    try:
        for component in target.parts[1:]:
            next_fd = os.open(component, flags, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        info = os.fstat(fd)
        if not stat.S_ISDIR(info.st_mode):
            raise WorkspaceStagingError("workspace_root_not_directory")
        return fd, info
    except Exception:
        os.close(fd)
        raise


def _normalize_paths(relative_paths: Iterable[str], limits: _Limits) -> list[tuple[str, ...]]:
    if isinstance(relative_paths, (str, bytes)):
        raise WorkspaceStagingError("workspace_file_allowlist_must_be_a_sequence")
    try:
        raw_paths = list(relative_paths)
    except TypeError as exc:
        raise WorkspaceStagingError("workspace_file_allowlist_invalid") from exc
    if not raw_paths or len(raw_paths) > limits.max_files:
        raise WorkspaceStagingError("workspace_file_count_out_of_range")

    normalized: list[tuple[str, ...]] = []
    seen: set[str] = set()
    for raw in raw_paths:
        if type(raw) is not str or not raw or "\x00" in raw or "\\" in raw:
            raise WorkspaceStagingError("workspace_relative_path_invalid")
        try:
            encoded = raw.encode("utf-8", errors="strict")
        except UnicodeError as exc:
            raise WorkspaceStagingError("workspace_relative_path_invalid_unicode") from exc
        if len(encoded) > limits.max_path_bytes:
            raise WorkspaceStagingError("workspace_relative_path_too_long")
        if unicodedata.normalize("NFC", raw) != raw:
            raise WorkspaceStagingError("workspace_relative_path_not_nfc")
        path = PurePosixPath(raw)
        parts = path.parts
        if path.is_absolute() or not parts or any(p in {"", ".", ".."} for p in parts):
            raise WorkspaceStagingError("workspace_relative_path_unsafe")
        if len(parts) > limits.max_depth:
            raise WorkspaceStagingError("workspace_relative_path_too_deep")
        key = "/".join(parts).casefold()
        if key in seen:
            raise WorkspaceStagingError("workspace_relative_path_collision")
        seen.add(key)
        normalized.append(tuple(parts))

    # Reject file/directory prefix collisions and case-folded directory aliases.
    keys = sorted("/".join(parts).casefold() for parts in normalized)
    for index, key in enumerate(keys):
        if index and key.startswith(keys[index - 1] + "/"):
            continue
    file_keys = set(keys)
    for key in keys:
        parent = key
        while "/" in parent:
            parent = parent.rsplit("/", 1)[0]
            if parent in file_keys:
                raise WorkspaceStagingError("workspace_file_directory_collision")
    return sorted(normalized, key=lambda parts: "/".join(parts).encode("utf-8"))


def _open_source_file(root_fd: int, parts: tuple[str, ...], root_device: int) -> tuple[int, os.stat_result]:
    current_fd = os.dup(root_fd)
    flags_dir = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    try:
        for component in parts[:-1]:
            next_fd = os.open(component, flags_dir, dir_fd=current_fd)
            info = os.fstat(next_fd)
            if not stat.S_ISDIR(info.st_mode) or info.st_dev != root_device:
                os.close(next_fd)
                raise WorkspaceStagingError("workspace_path_contains_unsafe_directory")
            os.close(current_fd)
            current_fd = next_fd
        flags_file = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
        file_fd = os.open(parts[-1], flags_file, dir_fd=current_fd)
        info = os.fstat(file_fd)
        if not stat.S_ISREG(info.st_mode):
            os.close(file_fd)
            raise WorkspaceStagingError("workspace_allowlist_entry_not_regular_file")
        if info.st_nlink != 1:
            os.close(file_fd)
            raise WorkspaceStagingError("workspace_hardlink_not_allowed")
        if info.st_uid != os.geteuid() or info.st_dev != root_device:
            os.close(file_fd)
            raise WorkspaceStagingError("workspace_file_owner_or_device_mismatch")
        return file_fd, info
    finally:
        os.close(current_fd)


def _ensure_output_directory(root_fd: int, parts: tuple[str, ...]) -> int:
    current_fd = os.dup(root_fd)
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    try:
        for component in parts:
            try:
                os.mkdir(component, 0o700, dir_fd=current_fd)
            except FileExistsError:
                pass
            next_fd = os.open(component, flags, dir_fd=current_fd)
            info = os.fstat(next_fd)
            if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid():
                os.close(next_fd)
                raise WorkspaceStagingError("workspace_staging_directory_unsafe")
            os.close(current_fd)
            current_fd = next_fd
        result = current_fd
        current_fd = -1
        return result
    finally:
        if current_fd >= 0:
            os.close(current_fd)


def stage_workspace_snapshot(
    source_root: str | Path,
    staging_root: str | Path,
    *,
    attempt_id: str,
    relative_paths: Iterable[str],
    max_files: int = 10_000,
    max_file_bytes: int = 10 * 1024 * 1024,
    max_total_bytes: int = 100 * 1024 * 1024,
    max_path_bytes: int = 1024,
    max_depth: int = 32,
) -> WorkspaceSnapshot:
    """Copy only explicitly allowlisted regular files into a sealed snapshot.

    The source is opened and traversed using descriptor-relative no-follow
    operations. Source files must be owned by the current user, single-linked,
    regular files on the workspace root device, and stable during copying.
    Existing attempt destinations are never overwritten.
    """
    _require_posix_fd_support()
    if type(attempt_id) is not str or not re.fullmatch(r"[A-Za-z0-9_-]{16,128}", attempt_id):
        raise WorkspaceStagingError("workspace_attempt_id_invalid")

    integer_limits = {
        "max_files": (max_files, 1, 100_000),
        "max_file_bytes": (max_file_bytes, 1, 1024 * 1024 * 1024),
        "max_total_bytes": (max_total_bytes, 1, 4 * 1024 * 1024 * 1024),
        "max_path_bytes": (max_path_bytes, 1, 16_384),
        "max_depth": (max_depth, 1, 128),
    }
    for name, (value, low, high) in integer_limits.items():
        if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
            raise WorkspaceStagingError("workspace_" + name + "_invalid")
    limits = _Limits(max_files, max_file_bytes, max_total_bytes, max_path_bytes, max_depth)

    paths = _normalize_paths(relative_paths, limits)
    try:
        destination_root = ensure_private_profile_directory(staging_root)
    except PrivateProfileError as exc:
        raise WorkspaceStagingError("workspace_staging_root_not_private") from exc

    source_fd = None
    destination_fd = None
    temporary_name = ".staging-" + secrets.token_hex(16)
    published = False
    try:
        source_fd, source_info = _open_absolute_directory(source_root)
        if source_info.st_uid != os.geteuid():
            raise WorkspaceStagingError("workspace_root_owner_mismatch")
        destination_fd = os.open(
            destination_root,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
        )
        os.mkdir(temporary_name, 0o700, dir_fd=destination_fd)
        temp_fd = os.open(
            temporary_name,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
            dir_fd=destination_fd,
        )
        try:
            entries = []
            total_bytes = 0
            for parts in paths:
                source_file_fd, before = _open_source_file(source_fd, parts, source_info.st_dev)
                parent_fd = _ensure_output_directory(temp_fd, parts[:-1])
                try:
                    target_fd = os.open(
                        parts[-1],
                        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
                        0o600,
                        dir_fd=parent_fd,
                    )
                    try:
                        digest = hashlib.sha256()
                        file_bytes = 0
                        while True:
                            chunk = os.read(source_file_fd, limits.chunk_bytes)
                            if not chunk:
                                break
                            file_bytes += len(chunk)
                            total_bytes += len(chunk)
                            if file_bytes > limits.max_file_bytes or total_bytes > limits.max_total_bytes:
                                raise WorkspaceStagingError("workspace_snapshot_size_limit_exceeded")
                            digest.update(chunk)
                            view = memoryview(chunk)
                            while view:
                                written = os.write(target_fd, view)
                                if written <= 0:
                                    raise WorkspaceStagingError("workspace_snapshot_write_failed")
                                view = view[written:]
                        after = os.fstat(source_file_fd)
                        stable_before = (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                        stable_after = (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
                        if stable_before != stable_after or file_bytes != before.st_size:
                            raise WorkspaceStagingError("workspace_source_changed_during_copy")
                        os.fchmod(target_fd, 0o400)
                        os.fsync(target_fd)
                    finally:
                        os.close(target_fd)
                finally:
                    os.close(parent_fd)
                    os.close(source_file_fd)
                entries.append({
                    "path": "/".join(parts),
                    "type": "file",
                    "mode": "0400",
                    "size": file_bytes,
                    "sha256": digest.hexdigest(),
                })

            manifest = {
                "schema": "yuki.workspace-snapshot.v1",
                "attempt_id": attempt_id,
                "files": entries,
                "file_count": len(entries),
                "total_bytes": total_bytes,
            }
            manifest_json = json.dumps(manifest, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
            manifest_bytes = manifest_json.encode("utf-8")
            manifest_digest = hashlib.sha256(manifest_bytes).hexdigest()
            manifest_fd = os.open(
                "manifest.json",
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
                0o400,
                dir_fd=temp_fd,
            )
            try:
                view = memoryview(manifest_bytes)
                while view:
                    written = os.write(manifest_fd, view)
                    if written <= 0:
                        raise WorkspaceStagingError("workspace_manifest_write_failed")
                    view = view[written:]
                os.fsync(manifest_fd)
            finally:
                os.close(manifest_fd)

            # Seal directories bottom-up. The snapshot remains owner-readable,
            # but no longer owner-writable; container mounts must also be read-only.
            for parts in sorted((p[:-1] for p in paths if len(p) > 1), key=len, reverse=True):
                directory_fd = _ensure_output_directory(temp_fd, parts)
                try:
                    os.fchmod(directory_fd, 0o500)
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            os.fchmod(temp_fd, 0o500)
            os.fsync(temp_fd)
        finally:
            os.close(temp_fd)

        # Destination name is the opaque attempt ID; rename is same-filesystem
        # and fails if a destination already exists.
        os.rename(temporary_name, attempt_id, src_dir_fd=destination_fd, dst_dir_fd=destination_fd)
        published = True
        os.fsync(destination_fd)
        final_path = str(destination_root / attempt_id)
        return WorkspaceSnapshot(attempt_id, final_path, manifest_digest, len(paths), total_bytes, manifest_json)
    except WorkspaceStagingError:
        raise
    except OSError as exc:
        raise WorkspaceStagingError("workspace_snapshot_creation_failed") from exc
    finally:
        if source_fd is not None:
            os.close(source_fd)
        if destination_fd is not None:
            if not published:
                try:
                    _remove_tree_at(destination_fd, temporary_name)
                except OSError:
                    pass
            os.close(destination_fd)


def _remove_tree_at(parent_fd: int, name: str) -> None:
    """Best-effort cleanup of a private unpublished staging directory."""
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    try:
        root_fd = os.open(name, flags, dir_fd=parent_fd)
    except FileNotFoundError:
        return
    try:
        for entry in os.listdir(root_fd):
            info = os.stat(entry, dir_fd=root_fd, follow_symlinks=False)
            if stat.S_ISDIR(info.st_mode):
                _remove_tree_at(root_fd, entry)
            else:
                os.unlink(entry, dir_fd=root_fd)
        os.fchmod(root_fd, 0o700)
    finally:
        os.close(root_fd)
    os.rmdir(name, dir_fd=parent_fd)
