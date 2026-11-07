"""Deterministic, bounded repository snapshots for reproducible verification.

This module handles an in-memory mapping of relative POSIX paths to file bytes.
It is a snapshot format/materializer, not a Git checkout, dependency resolver,
or OS sandbox. Callers must obtain the mapping from a trusted repository reader.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
from typing import Mapping

_SHA256 = re.compile(r"^[a-f0-9]{64}$")
_DEFAULT_EXCLUDED = frozenset({
    ".git", ".hg", ".svn", ".venv", "venv", "__pycache__",
    "node_modules", ".tox", ".mypy_cache", ".pytest_cache",
})


class RepositorySnapshotError(ValueError):
    """A repository snapshot is malformed, oversized, or unsafe to materialize."""


@dataclass(frozen=True)
class SnapshotFile:
    path: str
    sha256: str
    byte_length: int


@dataclass(frozen=True)
class RepositorySnapshot:
    snapshot_sha256: str
    files: tuple[SnapshotFile, ...]
    total_bytes: int


class RepositorySnapshotBuilder:
    """Build and materialize deterministic snapshots with strict size/path limits."""

    def __init__(
        self,
        *,
        max_files: int = 10_000,
        max_file_bytes: int = 16_000_000,
        max_total_bytes: int = 256_000_000,
        excluded_directories: frozenset[str] = _DEFAULT_EXCLUDED,
    ):
        for name, value, minimum in (
            ("max_files", max_files, 1),
            ("max_file_bytes", max_file_bytes, 1),
            ("max_total_bytes", max_total_bytes, 1),
        ):
            if type(value) is not int or value < minimum:
                raise ValueError(f"{name}_must_be_positive_integer")
        if not isinstance(excluded_directories, frozenset) or any(
            not isinstance(item, str) or not item or "/" in item or "\\" in item
            for item in excluded_directories
        ):
            raise ValueError("invalid_excluded_directories")
        self.max_files = max_files
        self.max_file_bytes = max_file_bytes
        self.max_total_bytes = max_total_bytes
        self.excluded_directories = excluded_directories

    @staticmethod
    def _validate_path(raw_path: str) -> str:
        if not isinstance(raw_path, str) or not raw_path or len(raw_path) > 1024:
            raise RepositorySnapshotError("invalid_snapshot_path")
        if "\x00" in raw_path or "\\" in raw_path:
            raise RepositorySnapshotError("invalid_snapshot_path")
        path = PurePosixPath(raw_path)
        if path.is_absolute() or any(part in {"", ".", ".."} for part in raw_path.split("/")):
            raise RepositorySnapshotError("snapshot_path_must_be_normalized_relative_posix")
        if path.as_posix() != raw_path or raw_path.startswith("./"):
            raise RepositorySnapshotError("snapshot_path_must_be_normalized_relative_posix")
        return raw_path

    def build(self, files: Mapping[str, bytes]) -> RepositorySnapshot:
        if not isinstance(files, Mapping):
            raise TypeError("files_must_be_a_mapping_of_paths_to_bytes")
        if len(files) > self.max_files:
            raise RepositorySnapshotError("snapshot_file_count_exceeded")
        records = []
        total = 0
        seen = set()
        for raw_path, content in files.items():
            path = self._validate_path(raw_path)
            parts = PurePosixPath(path).parts
            if any(part in self.excluded_directories for part in parts[:-1]):
                continue
            if path in seen:
                raise RepositorySnapshotError("duplicate_snapshot_path")
            seen.add(path)
            if not isinstance(content, bytes):
                raise TypeError("snapshot_file_content_must_be_bytes")
            size = len(content)
            if size > self.max_file_bytes:
                raise RepositorySnapshotError("snapshot_file_size_exceeded")
            total += size
            if total > self.max_total_bytes:
                raise RepositorySnapshotError("snapshot_total_size_exceeded")
            records.append(SnapshotFile(path, hashlib.sha256(content).hexdigest(), size))
        records.sort(key=lambda item: item.path)
        canonical = json.dumps(
            [{"path": item.path, "sha256": item.sha256, "byte_length": item.byte_length}
             for item in records],
            ensure_ascii=False, separators=(",", ":"), sort_keys=True,
        ).encode("utf-8")
        return RepositorySnapshot(hashlib.sha256(b"yuki-repository-snapshot-v1\\0" + canonical).hexdigest(),
                                  tuple(records), total)

    def materialize(
        self,
        files: Mapping[str, bytes],
        destination: str | Path,
        *,
        expected_snapshot_sha256: str | None = None,
    ) -> RepositorySnapshot:
        """Create a new workspace; never overwrite existing paths or follow symlinks."""
        snapshot = self.build(files)
        if expected_snapshot_sha256 is not None:
            if not isinstance(expected_snapshot_sha256, str) or not _SHA256.fullmatch(expected_snapshot_sha256):
                raise RepositorySnapshotError("invalid_expected_snapshot_digest")
            if snapshot.snapshot_sha256 != expected_snapshot_sha256:
                raise RepositorySnapshotError("snapshot_digest_mismatch")
        root = Path(destination).expanduser().absolute()
        if root.exists() and (root.is_symlink() or not root.is_dir() or any(root.iterdir())):
            raise RepositorySnapshotError("snapshot_destination_must_be_new_or_empty_directory")
        root.mkdir(parents=True, exist_ok=True)
        if root.is_symlink():
            raise RepositorySnapshotError("snapshot_destination_symlink_rejected")
        for record in snapshot.files:
            target = root.joinpath(*PurePosixPath(record.path).parts)
            parent = target.parent
            parent.mkdir(parents=True, exist_ok=True)
            current = root
            for part in PurePosixPath(record.path).parts[:-1]:
                current = current / part
                if current.is_symlink():
                    raise RepositorySnapshotError("snapshot_symlink_path_rejected")
            if target.exists() or target.is_symlink():
                raise RepositorySnapshotError("snapshot_target_already_exists")
            content = files[record.path]
            if hashlib.sha256(content).hexdigest() != record.sha256:
                raise RepositorySnapshotError("snapshot_source_changed_during_materialization")
            try:
                with target.open("xb") as stream:
                    stream.write(content)
            except FileExistsError as exc:
                raise RepositorySnapshotError("snapshot_target_already_exists") from exc
        return snapshot
