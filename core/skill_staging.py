"""Verify and safely stage immutable ZIP skill artifacts.

This is a filesystem preparation boundary, not a process sandbox. It never imports
skill code or launches a container. Callers must keep the returned context alive
until the container has stopped and cleanup has been confirmed.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import io
import os
from pathlib import Path, PurePosixPath
import re
import stat
import tempfile
import zipfile
from typing import Iterator

from core.skill_execution_manifest import (
    SkillExecutionManifest,
    SkillManifestError,
    verify_artifact_digest,
)


class SkillStagingError(ValueError):
    """An artifact cannot be safely verified or staged."""


_MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
_MAX_EXPANDED_BYTES = 256 * 1024 * 1024
_MAX_ENTRIES = 4096
_MAX_COMPRESSION_RATIO = 200
_RESERVED_WINDOWS = re.compile(
    r"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?$", re.IGNORECASE
)


@dataclass(frozen=True)
class StagedSkillArtifact:
    """Read-only invocation-specific tree and deterministic content digest."""

    root: Path
    tree_sha256: str
    artifact_sha256: str
    file_count: int
    expanded_bytes: int


def _safe_member_name(raw: str) -> tuple[str, ...]:
    if (not isinstance(raw, str) or not raw or len(raw) > 1024
            or "\x00" in raw or "\\" in raw or raw.startswith("/")
            or raw.startswith("//") or re.match(r"^[A-Za-z]:", raw)):
        raise SkillStagingError("archive contains an unsafe member path")
    name = raw[:-1] if raw.endswith("/") else raw
    parts = name.split("/")
    if (not name or len(parts) > 32
            or any(part in ("", ".", "..") for part in parts)):
        raise SkillStagingError("archive path traversal or empty component rejected")
    for part in parts:
        if (len(part) > 255 or part.endswith((".", " "))
                or any(ord(ch) < 32 or ord(ch) == 127 for ch in part)
                or ":" in part or _RESERVED_WINDOWS.fullmatch(part)):
            raise SkillStagingError("archive member is not portable across supported hosts")
    return tuple(parts)


def _entrypoint_file(manifest: SkillExecutionManifest, root: Path) -> Path:
    module, function = manifest.entrypoint.split(":", 1)
    del function
    relative = Path(*module.split("."))
    candidates = (root / relative.with_suffix(".py"), root / relative / "__init__.py")
    for candidate in candidates:
        try:
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(root)
        except (OSError, ValueError):
            continue
        if candidate.is_symlink() or not resolved.is_file():
            continue
        return resolved
    raise SkillStagingError("manifest entrypoint does not exist in the verified artifact")


def _tree_digest(root: Path) -> tuple[str, int, int]:
    records: list[tuple[str, int, str]] = []
    total = 0
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        if path.is_symlink():
            raise SkillStagingError("staged artifact unexpectedly contains a symlink")
        if path.is_dir():
            continue
        if not path.is_file():
            raise SkillStagingError("staged artifact contains a non-regular file")
        relative = path.relative_to(root).as_posix()
        digest = hashlib.sha256()
        size = 0
        with path.open("rb") as stream:
            while chunk := stream.read(1024 * 1024):
                size += len(chunk)
                total += len(chunk)
                digest.update(chunk)
        records.append((relative, size, digest.hexdigest()))
    encoded = "\n".join(f"{name}\0{size}\0{digest}" for name, size, digest in records).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest(), len(records), total


@contextmanager
def stage_skill_artifact(
    artifact: bytes,
    manifest: SkillExecutionManifest,
    *,
    staging_parent: str | Path,
) -> Iterator[StagedSkillArtifact]:
    """Verify exact ZIP bytes, reject unsafe entries, and yield a private read-only tree.

    Limits: 64 MiB compressed input, 256 MiB expanded data, 4096 entries,
    200:1 per-entry compression ratio. Symlinks, special files, encrypted
    members, duplicate/case-colliding paths, and unsupported compression fail closed.
    """
    if not isinstance(manifest, SkillExecutionManifest):
        raise SkillStagingError("validated skill manifest required")
    try:
        manifest.validate()
    except SkillManifestError as exc:
        raise SkillStagingError("skill manifest rejected") from exc
    if not isinstance(artifact, bytes) or not artifact or len(artifact) > _MAX_ARCHIVE_BYTES:
        raise SkillStagingError("skill artifact is empty or exceeds the archive size limit")
    if not verify_artifact_digest(artifact, manifest.artifact_sha256):
        raise SkillStagingError("skill artifact digest does not match the approved manifest")
    parent = Path(staging_parent)
    if parent.is_symlink():
        raise SkillStagingError("staging parent must not be a symlink")
    try:
        parent = parent.resolve(strict=True)
    except OSError as exc:
        raise SkillStagingError("staging parent must already exist") from exc
    if not parent.is_dir():
        raise SkillStagingError("staging parent must be a directory")

    with tempfile.TemporaryDirectory(prefix="yuki-skill-", dir=parent) as temporary:
        root = Path(temporary)
        if os.name != "nt":
            os.chmod(root, 0o700)
        seen: set[str] = set()
        files: set[tuple[str, ...]] = set()
        directories: set[tuple[str, ...]] = {()}
        expanded = 0
        try:
            archive = zipfile.ZipFile(io.BytesIO(artifact), "r")
        except (zipfile.BadZipFile, OSError) as exc:
            raise SkillStagingError("skill artifact is not a valid ZIP archive") from exc
        with archive:
            infos = archive.infolist()
            if not infos or len(infos) > _MAX_ENTRIES:
                raise SkillStagingError("archive entry count is outside permitted bounds")
            for info in infos:
                parts = _safe_member_name(info.filename)
                key = "/".join(parts).casefold()
                if key in seen:
                    raise SkillStagingError("archive contains duplicate or case-colliding paths")
                seen.add(key)
                mode = (info.external_attr >> 16) & 0xFFFF
                kind = stat.S_IFMT(mode)
                is_dir = info.is_dir()
                if kind not in (0, stat.S_IFREG, stat.S_IFDIR):
                    raise SkillStagingError("archive symlinks and special files are forbidden")
                if is_dir and kind not in (0, stat.S_IFDIR):
                    raise SkillStagingError("archive directory type mismatch")
                if not is_dir and kind == stat.S_IFDIR:
                    raise SkillStagingError("archive file type mismatch")
                if info.flag_bits & 0x1:
                    raise SkillStagingError("encrypted archive members are forbidden")
                if info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
                    raise SkillStagingError("unsupported archive compression method")
                if info.file_size < 0 or info.compress_size < 0:
                    raise SkillStagingError("invalid archive member size")
                if info.file_size and info.compress_size == 0:
                    raise SkillStagingError("invalid archive compression ratio")
                if info.compress_size and info.file_size / info.compress_size > _MAX_COMPRESSION_RATIO:
                    raise SkillStagingError("archive member compression ratio exceeds limit")
                expanded += info.file_size
                if expanded > _MAX_EXPANDED_BYTES:
                    raise SkillStagingError("expanded archive exceeds size limit")
                target = root.joinpath(*parts)
                if is_dir:
                    directories.add(parts)
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                files.add(parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
                if hasattr(os, "O_NOFOLLOW"):
                    flags |= os.O_NOFOLLOW
                try:
                    fd = os.open(target, flags, 0o600)
                except OSError as exc:
                    raise SkillStagingError("could not safely create staged artifact file") from exc
                written = 0
                try:
                    with os.fdopen(fd, "wb") as output, archive.open(info, "r") as source:
                        while chunk := source.read(1024 * 1024):
                            written += len(chunk)
                            if written > info.file_size or written > _MAX_EXPANDED_BYTES:
                                raise SkillStagingError("archive member expanded beyond declared size")
                            output.write(chunk)
                    if written != info.file_size:
                        raise SkillStagingError("archive member size did not match its directory record")
                except Exception:
                    try:
                        target.unlink(missing_ok=True)
                    except OSError:
                        pass
                    raise

        for file_parts in files:
            for index in range(1, len(file_parts)):
                parent_parts = file_parts[:index]
                if parent_parts in files:
                    raise SkillStagingError("archive file/directory path collision")
        _entrypoint_file(manifest, root)
        tree_sha, file_count, expanded_bytes = _tree_digest(root)
        for path in sorted(root.rglob("*"), key=lambda item: len(item.parts), reverse=True):
            if path.is_dir() and os.name != "nt":
                os.chmod(path, 0o500)
            elif path.is_file() and os.name != "nt":
                os.chmod(path, 0o400)
        if os.name != "nt":
            os.chmod(root, 0o500)
        yield StagedSkillArtifact(
            root=root,
            tree_sha256=tree_sha,
            artifact_sha256=manifest.artifact_sha256,
            file_count=file_count,
            expanded_bytes=expanded_bytes,
        )
