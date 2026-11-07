"""Bounded, read-only inventory for explicitly authorized application roots.

The scanner records file metadata and SHA-256 digests only. It does not parse
untrusted manifests, follow symlinks, execute targets, or access the network.
"""
from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path
from typing import Any


DEFAULT_MAX_FILES = 20_000
DEFAULT_MAX_FILE_BYTES = 64 * 1024 * 1024
DEFAULT_MAX_TOTAL_BYTES = 512 * 1024 * 1024
DEFAULT_CHUNK_BYTES = 1024 * 1024
MANIFEST_NAMES = frozenset({
    "package.json", "package-lock.json", "pnpm-lock.yaml", "yarn.lock",
    "pyproject.toml", "requirements.txt", "poetry.lock", "Pipfile",
    "Pipfile.lock", "Cargo.toml", "Cargo.lock", "go.mod", "go.sum",
    "Gemfile", "Gemfile.lock", "composer.json", "composer.lock",
    "pom.xml", "build.gradle", "build.gradle.kts",
})
SKIP_DIRECTORIES = frozenset({".git", ".hg", ".svn", "__pycache__", ".venv", "venv", "node_modules"})


class ArtifactInventoryError(ValueError):
    """Raised when an inventory request or target violates scanner policy."""


def _positive_int(value: int, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ArtifactInventoryError(f"{name} must be a positive integer.")
    return value


def _file_type(name: str) -> str:
    suffix = Path(name).suffix.lower()
    if suffix in {".py", ".pyi"}:
        return "python_source"
    if suffix in {".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx"}:
        return "javascript_source"
    if suffix in {".html", ".htm", ".css"}:
        return "web_asset"
    if suffix in {".exe", ".dll", ".so", ".dylib", ".bin"}:
        return "binary_candidate"
    if suffix in {".zip", ".tar", ".gz", ".7z", ".rar"}:
        return "archive_candidate"
    if suffix in {".gguf", ".safetensors", ".onnx", ".pt", ".pth"}:
        return "model_candidate"
    return "file"


def _hash_regular_file(path: Path, expected: os.stat_result, chunk_bytes: int) -> str:
    flags = os.O_RDONLY
    flags |= getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(str(path), flags)
    except OSError as exc:
        raise ArtifactInventoryError(f"Unable to safely open file: {path.name}") from exc
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise ArtifactInventoryError(f"File changed type during inventory: {path.name}")
        if (opened.st_dev, opened.st_ino, opened.st_size) != (
            expected.st_dev, expected.st_ino, expected.st_size
        ):
            raise ArtifactInventoryError(f"File changed during inventory: {path.name}")
        digest = hashlib.sha256()
        while True:
            chunk = os.read(descriptor, chunk_bytes)
            if not chunk:
                break
            digest.update(chunk)
        after = os.fstat(descriptor)
        if (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) != (
            opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns
        ):
            raise ArtifactInventoryError(f"File changed while hashing: {path.name}")
        return digest.hexdigest()
    finally:
        os.close(descriptor)


def inventory_artifact_root(
    root: str | os.PathLike[str],
    *,
    max_files: int = DEFAULT_MAX_FILES,
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
    max_total_bytes: int = DEFAULT_MAX_TOTAL_BYTES,
    chunk_bytes: int = DEFAULT_CHUNK_BYTES,
    excluded_directories: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Inventory one existing directory without following links or parsing files."""
    max_files = _positive_int(max_files, "max_files")
    max_file_bytes = _positive_int(max_file_bytes, "max_file_bytes")
    max_total_bytes = _positive_int(max_total_bytes, "max_total_bytes")
    chunk_bytes = _positive_int(chunk_bytes, "chunk_bytes")
    if chunk_bytes > 8 * 1024 * 1024:
        raise ArtifactInventoryError("chunk_bytes exceeds the 8 MiB safety limit.")
    if not isinstance(excluded_directories, tuple) or any(
        not isinstance(item, str) or not item or Path(item).name != item
        for item in excluded_directories
    ):
        raise ArtifactInventoryError("excluded_directories must contain simple directory names.")

    requested = Path(root).expanduser()
    try:
        resolved_root = requested.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ArtifactInventoryError("Inventory root must resolve to an existing directory.") from exc
    if not resolved_root.is_dir():
        raise ArtifactInventoryError("Inventory root must be a directory.")

    exclusions = SKIP_DIRECTORIES.union(excluded_directories)
    pending = [resolved_root]
    entries: list[dict[str, Any]] = []
    skipped_symlinks = 0
    skipped_special = 0
    total_bytes = 0

    while pending:
        directory = pending.pop()
        try:
            children = sorted(os.scandir(directory), key=lambda item: item.name.casefold())
        except OSError as exc:
            raise ArtifactInventoryError(f"Unable to enumerate directory: {directory.name}") from exc
        for child in children:
            if child.name in exclusions:
                continue
            child_path = Path(child.path)
            try:
                metadata = child.stat(follow_symlinks=False)
            except OSError as exc:
                raise ArtifactInventoryError(f"Unable to inspect entry: {child.name}") from exc
            if stat.S_ISLNK(metadata.st_mode):
                skipped_symlinks += 1
                continue
            if stat.S_ISDIR(metadata.st_mode):
                pending.append(child_path)
                continue
            if not stat.S_ISREG(metadata.st_mode):
                skipped_special += 1
                continue
            relative = child_path.relative_to(resolved_root).as_posix()
            if len(entries) >= max_files:
                raise ArtifactInventoryError("Inventory exceeds max_files.")
            if metadata.st_size > max_file_bytes:
                raise ArtifactInventoryError(f"File exceeds max_file_bytes: {relative}")
            if total_bytes + metadata.st_size > max_total_bytes:
                raise ArtifactInventoryError("Inventory exceeds max_total_bytes.")
            digest = _hash_regular_file(child_path, metadata, chunk_bytes)
            try:
                current = child_path.lstat()
            except OSError as exc:
                raise ArtifactInventoryError(f"File disappeared during inventory: {relative}") from exc
            if stat.S_ISLNK(current.st_mode) or (
                current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns
            ) != (metadata.st_dev, metadata.st_ino, metadata.st_size, metadata.st_mtime_ns):
                raise ArtifactInventoryError(f"File changed during inventory: {relative}")
            total_bytes += metadata.st_size
            entries.append({
                "path": relative,
                "size_bytes": metadata.st_size,
                "sha256": digest,
                "kind": _file_type(child.name),
                "manifest": child.name in MANIFEST_NAMES,
            })

    entries.sort(key=lambda item: item["path"].casefold())
    manifests = [item["path"] for item in entries if item["manifest"]]
    payload = {
        "root_label": resolved_root.name or "authorized-root",
        "file_count": len(entries),
        "total_bytes": total_bytes,
        "manifest_paths": manifests,
        "skipped_symlinks": skipped_symlinks,
        "skipped_special_entries": skipped_special,
        "files": entries,
        "limitations": [
            "Inventory records file names, sizes and hashes; it does not parse file contents.",
            "Ignored directories are excluded from the observed scope.",
            "A static inventory does not establish runtime behavior or prove that a dependency is used.",
        ],
        "coverage": {
            "status": "partial" if exclusions else "complete",
            "scope": "regular-files-under-authorized-root",
        },
    }
    return payload
