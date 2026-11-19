"""Validate untrusted sandbox artifacts in a private staging directory.

This module does not extract archives, execute files, approve changes, or promote
artifacts into the trusted workspace. The caller must provide a staging directory
that is no longer writable by the sandbox while validation runs.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import Sequence


_MAX_FILES = 100
_MAX_FILE_BYTES = 10 * 1024 * 1024
_MAX_TOTAL_BYTES = 50 * 1024 * 1024
_CHUNK_BYTES = 64 * 1024
_BLOCKED_PARTS = frozenset({
    ".git", ".ssh", ".aws", ".azure", ".config", "__pycache__",
    "node_modules", ".venv", "venv",
})
_SECRET_NAME = re.compile(
    r"(^\.env($|\.)|secret|credential|password|token|private.?key|id_rsa|id_ed25519)",
    re.IGNORECASE,
)


class ArtifactValidationError(ValueError):
    """An artifact set violates staging or content policy."""


@dataclass(frozen=True)
class ArtifactFile:
    path: str
    size_bytes: int
    sha256: str


@dataclass(frozen=True)
class ArtifactManifest:
    files: tuple[ArtifactFile, ...]
    total_bytes: int
    digest: str


def _validate_extensions(extensions: Sequence[str]) -> frozenset[str]:
    if not isinstance(extensions, (tuple, list, set, frozenset)) or not extensions:
        raise ArtifactValidationError("an explicit artifact extension allow-list is required")
    normalized = set()
    for extension in extensions:
        if not isinstance(extension, str) or not re.fullmatch(r"\.[a-z0-9]{1,12}", extension.lower()):
            raise ArtifactValidationError("artifact extensions must be simple dotted suffixes")
        normalized.add(extension.lower())
    return frozenset(normalized)


def _hash_regular_file(path: Path, max_bytes: int) -> tuple[int, str]:
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode):
        raise ArtifactValidationError("artifact entries must be regular files")
    if before.st_nlink != 1:
        raise ArtifactValidationError("hard-linked artifacts are not allowed")
    if before.st_size > max_bytes:
        raise ArtifactValidationError("artifact exceeds the per-file size limit")

    digest = hashlib.sha256()
    size = 0
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(str(path), flags)
    except OSError as exc:
        raise ArtifactValidationError("artifact could not be opened safely") from exc
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1:
            raise ArtifactValidationError("artifact changed to an unsafe filesystem object")
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise ArtifactValidationError("artifact changed during validation")
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            while True:
                chunk = handle.read(_CHUNK_BYTES)
                if not chunk:
                    break
                size += len(chunk)
                if size > max_bytes:
                    raise ArtifactValidationError("artifact exceeds the per-file size limit")
                digest.update(chunk)
        after = os.fstat(descriptor)
        if (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) != (
            opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns
        ):
            raise ArtifactValidationError("artifact changed while being validated")
    finally:
        os.close(descriptor)
    return size, digest.hexdigest()


def validate_artifact_directory(
    staging_directory: str | Path,
    *,
    allowed_extensions: Sequence[str],
    max_files: int = _MAX_FILES,
    max_file_bytes: int = _MAX_FILE_BYTES,
    max_total_bytes: int = _MAX_TOTAL_BYTES,
) -> ArtifactManifest:
    """Return a deterministic digest manifest for safe, bounded staged files.

    The directory must be private to the trusted host process and frozen against
    sandbox writes for the duration of this call. This function rejects symlinks,
    hard links, special files, secret-like names, and unapproved extensions.
    """
    extensions = _validate_extensions(allowed_extensions)
    for name, value, minimum, maximum in (
        ("max_files", max_files, 1, _MAX_FILES),
        ("max_file_bytes", max_file_bytes, 1, _MAX_FILE_BYTES),
        ("max_total_bytes", max_total_bytes, 1, _MAX_TOTAL_BYTES),
    ):
        if type(value) is not int or not minimum <= value <= maximum:
            raise ArtifactValidationError(f"{name} is outside the permitted range")

    supplied_root = Path(staging_directory).expanduser()
    if supplied_root.is_symlink():
        raise ArtifactValidationError("staging directory cannot be a symlink")
    try:
        root = supplied_root.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ArtifactValidationError("staging directory must exist") from exc
    if not root.is_dir():
        raise ArtifactValidationError("staging path must be a directory")

    artifacts = []
    total = 0
    for current, directory_names, file_names in os.walk(root, topdown=True, followlinks=False):
        current_path = Path(current)
        for name in list(directory_names):
            entry = current_path / name
            if entry.is_symlink() or not entry.is_dir():
                raise ArtifactValidationError("symlink and non-directory entries are not allowed")
            if name in _BLOCKED_PARTS or _SECRET_NAME.search(name):
                raise ArtifactValidationError("artifact path is excluded by policy")
        for name in file_names:
            entry = current_path / name
            relative = entry.relative_to(root)
            if any(part in _BLOCKED_PARTS or _SECRET_NAME.search(part) for part in relative.parts):
                raise ArtifactValidationError("artifact path is excluded by policy")
            if entry.is_symlink():
                raise ArtifactValidationError("symlink artifacts are not allowed")
            if entry.suffix.lower() not in extensions:
                raise ArtifactValidationError("artifact extension is not allow-listed")
            if len(artifacts) >= max_files:
                raise ArtifactValidationError("artifact count limit exceeded")
            size, file_digest = _hash_regular_file(entry, max_file_bytes)
            total += size
            if total > max_total_bytes:
                raise ArtifactValidationError("artifact set exceeds the total size limit")
            artifacts.append(ArtifactFile(relative.as_posix(), size, file_digest))

    if not artifacts:
        raise ArtifactValidationError("no artifacts were produced")
    artifacts.sort(key=lambda item: item.path)
    manifest_data = [
        {"path": item.path, "size_bytes": item.size_bytes, "sha256": item.sha256}
        for item in artifacts
    ]
    manifest_digest = hashlib.sha256(
        json.dumps(manifest_data, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return ArtifactManifest(tuple(artifacts), total, manifest_digest)
