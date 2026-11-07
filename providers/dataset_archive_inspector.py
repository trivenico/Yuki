"""Bounded, read-only inspection of untrusted dataset archives.

Inspection uses one no-follow file descriptor for hashing and parsing. No member
is extracted or executed; the inventory is metadata, not content validation.
"""
from __future__ import annotations

import errno
import hashlib
import hmac
import os
import re
import stat
import tarfile
import zipfile
from pathlib import PurePosixPath, PureWindowsPath


class DatasetArchiveError(ValueError):
    """Archive is malformed or violates the safe intake policy."""


_DEFAULT_MAX_MEMBERS = 100_000
_DEFAULT_MAX_MEMBER_BYTES = 2 * 1024**3
_DEFAULT_MAX_TOTAL_BYTES = 20 * 1024**3
_DEFAULT_MAX_ARCHIVE_BYTES = 2 * 1024**3
_SHA256 = re.compile(r"^[a-fA-F0-9]{64}$")


def _safe_member_name(name: str) -> str:
    if not isinstance(name, str) or not name or "\x00" in name:
        raise DatasetArchiveError("invalid_archive_member_name")
    normalized = name.replace("\\", "/")
    normalized = normalized.rstrip("/")
    if not normalized:
        raise DatasetArchiveError("invalid_archive_member_name")
    windows = PureWindowsPath(name)
    path = PurePosixPath(normalized)
    if (normalized.startswith("/") or path.is_absolute() or windows.is_absolute()
            or windows.drive or any(part in {"", ".", ".."} for part in normalized.split("/"))):
        raise DatasetArchiveError("unsafe_archive_member_path")
    if re.match(r"^[A-Za-z]:", normalized):
        raise DatasetArchiveError("unsafe_archive_member_path")
    return normalized.rstrip("/")


def _limits(max_members, max_member_bytes, max_total_bytes, max_archive_bytes):
    for name, value in (
        ("max_members", max_members),
        ("max_member_bytes", max_member_bytes),
        ("max_total_bytes", max_total_bytes),
        ("max_archive_bytes", max_archive_bytes),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise DatasetArchiveError("invalid_" + name)


def inspect_dataset_archive(
    path, *, expected_sha256=None, max_members=_DEFAULT_MAX_MEMBERS,
    max_member_bytes=_DEFAULT_MAX_MEMBER_BYTES,
    max_total_bytes=_DEFAULT_MAX_TOTAL_BYTES,
    max_archive_bytes=_DEFAULT_MAX_ARCHIVE_BYTES,
):
    """Verify bytes and inspect ZIP/TAR metadata through one stable file handle."""
    _limits(max_members, max_member_bytes, max_total_bytes, max_archive_bytes)
    if expected_sha256 is not None and (
        not isinstance(expected_sha256, str) or not _SHA256.fullmatch(expected_sha256)
    ):
        raise DatasetArchiveError("invalid_expected_sha256")

    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise DatasetArchiveError("archive_must_be_regular_non_symlink_file") from exc
        raise DatasetArchiveError("archive_unreadable_or_unsafe") from exc

    try:
        with os.fdopen(fd, "rb") as archive_file:
            opened = os.fstat(archive_file.fileno())
            if not stat.S_ISREG(opened.st_mode):
                raise DatasetArchiveError("archive_must_be_regular_non_symlink_file")
            if opened.st_size < 1:
                raise DatasetArchiveError("empty_archive_rejected")
            if opened.st_size > max_archive_bytes:
                raise DatasetArchiveError("archive_compressed_size_limit_exceeded")

            digest = hashlib.sha256()
            while True:
                chunk = archive_file.read(1024 * 1024)
                if not chunk:
                    break
                digest.update(chunk)
            actual_sha256 = digest.hexdigest()
            if expected_sha256 is not None and not hmac.compare_digest(
                actual_sha256, expected_sha256.lower()
            ):
                raise DatasetArchiveError("archive_sha256_mismatch")

            archive_file.seek(0)
            entries, seen, total = [], set(), 0

            def add(name, size, kind):
                nonlocal total
                safe_name = _safe_member_name(name)
                if safe_name in seen:
                    raise DatasetArchiveError("duplicate_archive_member")
                seen.add(safe_name)
                if kind not in {"file", "directory"}:
                    raise DatasetArchiveError("unsupported_archive_member_type")
                if isinstance(size, bool) or not isinstance(size, int) or size < 0:
                    raise DatasetArchiveError("invalid_archive_member_size")
                if size > max_member_bytes:
                    raise DatasetArchiveError("archive_member_size_limit_exceeded")
                total += size
                if total > max_total_bytes:
                    raise DatasetArchiveError("archive_total_size_limit_exceeded")
                if len(entries) >= max_members:
                    raise DatasetArchiveError("archive_member_count_limit_exceeded")
                entries.append({"path": safe_name, "size_bytes": size, "type": kind})

            try:
                if zipfile.is_zipfile(archive_file):
                    archive_file.seek(0)
                    with zipfile.ZipFile(archive_file) as archive:
                        for item in archive.infolist():
                            mode = item.external_attr >> 16
                            file_type = stat.S_IFMT(mode)
                            if item.flag_bits & 0x1:
                                raise DatasetArchiveError("encrypted_archive_member_rejected")
                            if file_type == stat.S_IFLNK or (
                                file_type not in (0, stat.S_IFREG, stat.S_IFDIR)
                            ):
                                raise DatasetArchiveError("unsupported_archive_member_type")
                            is_dir = item.is_dir()
                            add(item.filename, 0 if is_dir else item.file_size,
                                "directory" if is_dir else "file")
                    archive_file.seek(0)
                    archive_format = "zip"
                else:
                    archive_file.seek(0)
                    with tarfile.open(fileobj=archive_file, mode="r:*") as archive:
                        for item in archive:
                            if item.isdir():
                                kind = "directory"
                            elif item.isfile():
                                kind = "file"
                            else:
                                raise DatasetArchiveError("unsupported_archive_member_type")
                            add(item.name, 0 if kind == "directory" else item.size, kind)
                    archive_format = "tar"
            except DatasetArchiveError:
                raise
            except (OSError, tarfile.TarError, zipfile.BadZipFile, EOFError) as exc:
                raise DatasetArchiveError("invalid_or_corrupt_archive") from exc

            after = os.fstat(archive_file.fileno())
            try:
                current_path = os.stat(path, follow_symlinks=False)
            except OSError as exc:
                raise DatasetArchiveError("archive_path_changed_during_inspection") from exc
            identity_before = (opened.st_dev, opened.st_ino)
            identity_after = (after.st_dev, after.st_ino)
            identity_path = (current_path.st_dev, current_path.st_ino)
            stable_fields = ("st_size", "st_mtime_ns", "st_ctime_ns")
            if not stat.S_ISREG(current_path.st_mode):
                raise DatasetArchiveError("archive_must_be_regular_non_symlink_file")
            if (identity_before != identity_after or identity_before != identity_path
                    or any(getattr(opened, field) != getattr(after, field)
                           for field in stable_fields)):
                raise DatasetArchiveError("archive_changed_during_inspection")

            return {
                "format": archive_format,
                "sha256": actual_sha256,
                "archive_size_bytes": opened.st_size,
                "member_count": len(entries),
                "total_uncompressed_bytes": total,
                "entries": entries,
            }
    except DatasetArchiveError:
        raise
    except OSError as exc:
        raise DatasetArchiveError("archive_unreadable_or_unsafe") from exc
