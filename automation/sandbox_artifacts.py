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
import tempfile
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


def _safe_relative_path(value: str) -> Path:
    if not isinstance(value, str) or not value.strip() or len(value) > 1000 or "\x00" in value or "\\\\" in value:
    if not isinstance(value, str) or not value.strip() or len(value) > 1000 or "\x00" in value or "\\" in value:
    relative = Path(value)
    if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
        raise ArtifactValidationError("absolute paths and traversal are not allowed")
    if any(part in _BLOCKED_PARTS or _SECRET_NAME.search(part) for part in relative.parts):
        raise ArtifactValidationError("destination is excluded by policy")
    return relative


class ApprovedArtifactPromotion:
    """Promote one validated artifact after digest- and destination-bound approval.

    Destination parent directories must already exist. Existing files are never
    overwritten. The trusted workspace must not be writable by untrusted actors.
    """

    def __init__(self, workspace: str | Path, *,
                 trusted_principal, approve_promotion, audit):
        from automation.authorized_read_tools import ToolPrincipal
        root = Path(workspace).expanduser()
        if root.is_symlink():
            raise ValueError("workspace root cannot be a symlink")
        root = root.resolve(strict=True)
        if not root.is_dir():
            raise ValueError("workspace must be an existing directory")
        if not all(callable(fn) for fn in (trusted_principal, approve_promotion, audit)):
            raise TypeError("trusted principal, approval, and audit callbacks are required")
        self.root = root
        self.trusted_principal = trusted_principal
        self.approve_promotion = approve_promotion
        self.audit = audit

    def _record(self, actor: str, outcome: str, digest: str,
                source: str, destination: str) -> None:
        try:
            self.audit({
                "actor_id": actor[:200], "action": "sandbox.artifact.promote",
                "outcome": outcome, "approval_digest": digest,
                "source": source[:1000], "destination": destination[:1000],
            })
        except Exception as exc:
            raise RuntimeError("audit unavailable; artifact promotion denied") from exc

    def promote(self, *, principal, staging_directory: str | Path,
                manifest: ArtifactManifest, artifact_path: str,
                destination_path: str) -> str:
        from automation.authorized_read_tools import ToolPrincipal, ToolAuthorizationError

        if not isinstance(principal, ToolPrincipal):
            raise ToolAuthorizationError("trusted authenticated principal required")
        try:
            trusted = self.trusted_principal(principal) is True
        except Exception:
            trusted = False
        if (not trusted or not isinstance(principal.actor_id, str)
                or not principal.actor_id.strip()
                or not isinstance(principal.permissions, frozenset)
                or "workspace:write" not in principal.permissions):
            raise ToolAuthorizationError("trusted workspace write permission is required")
        if not isinstance(manifest, ArtifactManifest) or not re.fullmatch(r"[0-9a-f]{64}", manifest.digest):
            raise ArtifactValidationError("a validated artifact manifest is required")
        if not isinstance(manifest.files, tuple) or not manifest.files:
            raise ArtifactValidationError("artifact manifest entries are invalid")
        canonical_entries = []
        seen_paths = set()
        calculated_total = 0
        for entry in manifest.files:
            if (not isinstance(entry, ArtifactFile)
                    or type(entry.size_bytes) is not int
                    or not 0 <= entry.size_bytes <= _MAX_FILE_BYTES
                    or not isinstance(entry.sha256, str)
                    or not re.fullmatch(r"[0-9a-f]{64}", entry.sha256)):
                raise ArtifactValidationError("artifact manifest entry is invalid")
            entry_path = _safe_relative_path(entry.path).as_posix()
            if entry_path in seen_paths:
                raise ArtifactValidationError("artifact manifest contains duplicate paths")
            seen_paths.add(entry_path)
            calculated_total += entry.size_bytes
            canonical_entries.append({
                "path": entry_path, "size_bytes": entry.size_bytes, "sha256": entry.sha256
            })
        calculated_digest = hashlib.sha256(
            json.dumps(canonical_entries, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        if (calculated_total != manifest.total_bytes or calculated_total > _MAX_TOTAL_BYTES
                or calculated_digest != manifest.digest):
            raise ArtifactValidationError("artifact manifest integrity check failed")

        source_rel = _safe_relative_path(artifact_path)
        destination_rel = _safe_relative_path(destination_path)
        item = next((entry for entry in manifest.files if entry.path == source_rel.as_posix()), None)
        if item is None:
            raise ArtifactValidationError("artifact is not part of the approved manifest")
        if not re.fullmatch(r"[0-9a-f]{64}", item.sha256) or type(item.size_bytes) is not int:
            raise ArtifactValidationError("manifest entry is invalid")

        staging = Path(staging_directory).expanduser()
        if staging.is_symlink():
            raise ArtifactValidationError("staging directory cannot be a symlink")
        staging = staging.resolve(strict=True)
        source = staging / source_rel
        cursor = staging
        for part in source_rel.parts:
            cursor = cursor / part
            if cursor.is_symlink():
                raise ArtifactValidationError("artifact source path cannot contain symlinks")
        destination = self.root / destination_rel
        cursor = self.root
        for part in destination_rel.parts[:-1]:
            cursor = cursor / part
            if cursor.is_symlink() or not cursor.is_dir():
                raise ArtifactValidationError("destination parent must be an existing real directory")
        parent = destination.parent.resolve(strict=True)
        try:
            parent.relative_to(self.root)
        except ValueError as exc:
            raise ArtifactValidationError("destination escapes trusted workspace") from exc
        if destination.exists() or destination.is_symlink():
            raise ArtifactValidationError("destination already exists; overwrites are forbidden")

        approval_data = {
            "manifest_digest": manifest.digest,
            "artifact_path": source_rel.as_posix(),
            "artifact_sha256": item.sha256,
            "destination_path": destination_rel.as_posix(),
        }
        approval_digest = hashlib.sha256(
            json.dumps(approval_data, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        promotion_id = "prom_" + approval_digest[:24]
        self._record(principal.actor_id, "requested", approval_digest,
                     source_rel.as_posix(), destination_rel.as_posix())
        try:
            approved = self.approve_promotion(principal.actor_id, promotion_id, approval_digest) is True
        except Exception:
            approved = False
        if not approved:
            self._record(principal.actor_id, "denied", approval_digest,
                         source_rel.as_posix(), destination_rel.as_posix())
            raise ToolAuthorizationError("explicit approval for this artifact and destination is required")

        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            source_fd = os.open(str(source), flags)
        except OSError as exc:
            raise ArtifactValidationError("artifact source could not be opened safely") from exc

        temporary_path = None
        promoted = False
        try:
            source_stat = os.fstat(source_fd)
            if not stat.S_ISREG(source_stat.st_mode) or source_stat.st_nlink != 1:
                raise ArtifactValidationError("artifact source is not a regular unlinked file")
            if source_stat.st_size != item.size_bytes or source_stat.st_size > _MAX_FILE_BYTES:
                raise ArtifactValidationError("artifact size no longer matches approved manifest")
            file_digest = hashlib.sha256()
            while True:
                chunk = os.read(source_fd, _CHUNK_BYTES)
                if not chunk:
                    break
                file_digest.update(chunk)
            if file_digest.hexdigest() != item.sha256:
                raise ArtifactValidationError("artifact content changed after approval")
            os.lseek(source_fd, 0, os.SEEK_SET)

            # Write to a private temporary file in the destination directory. The
            # final name appears only after the complete file is fsynced and linked.
            temp_fd, temp_name = tempfile.mkstemp(prefix=".yuki-promote-", dir=str(parent))
            temporary_path = Path(temp_name)
            copied_digest = hashlib.sha256()
            copied_size = 0
            try:
                while True:
                    chunk = os.read(source_fd, _CHUNK_BYTES)
                    if not chunk:
                        break
                    copied_size += len(chunk)
                    copied_digest.update(chunk)
                    view = memoryview(chunk)
                    while view:
                        written = os.write(temp_fd, view)
                        if written <= 0:
                            raise OSError("short write during artifact promotion")
                        view = view[written:]
                os.fsync(temp_fd)
            finally:
                os.close(temp_fd)

            after = os.fstat(source_fd)
            if (copied_size != item.size_bytes or copied_digest.hexdigest() != item.sha256
                    or (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
                    != (source_stat.st_dev, source_stat.st_ino, source_stat.st_size, source_stat.st_mtime_ns)):
                raise ArtifactValidationError("artifact changed during promotion")

            # link() is an atomic no-overwrite publication on the same filesystem.
            try:
                os.link(str(temporary_path), str(destination), follow_symlinks=False)
            except FileExistsError as exc:
                raise ArtifactValidationError("destination already exists; overwrites are forbidden") from exc
            promoted = True
            temporary_path.unlink()
            temporary_path = None
            self._record(principal.actor_id, "succeeded", approval_digest,
                         source_rel.as_posix(), destination_rel.as_posix())
            return approval_digest
        except Exception:
            if promoted:
                try:
                    destination.unlink(missing_ok=True)
                except OSError:
                    pass
            raise
        finally:
            os.close(source_fd)
            if temporary_path is not None:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError:
                    pass
