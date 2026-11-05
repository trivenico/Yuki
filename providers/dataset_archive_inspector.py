"""Read-only archive inspection for untrusted dataset intake.

No member is extracted or executed. Links, special files, unsafe paths, encrypted
ZIP entries, duplicate names, and resource-limit violations are rejected.
"""
from __future__ import annotations

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


def _safe_member_name(name: str) -> str:
    if not isinstance(name, str) or not name or "\x00" in name:
        raise DatasetArchiveError("invalid_archive_member_name")
    normalized = name.replace("\\", "/")
    windows = PureWindowsPath(name)
    path = PurePosixPath(normalized)
    if (normalized.startswith("/") or path.is_absolute() or windows.is_absolute()
            or windows.drive or any(part in {"", ".", ".."} for part in normalized.split("/"))):
        raise DatasetArchiveError("unsafe_archive_member_path")
    if re.match(r"^[A-Za-z]:", normalized):
        raise DatasetArchiveError("unsafe_archive_member_path")
    return normalized.rstrip("/")


def _limits(max_members, max_member_bytes, max_total_bytes):
    for name, value, minimum in (
        ("max_members", max_members, 1),
        ("max_member_bytes", max_member_bytes, 1),
        ("max_total_bytes", max_total_bytes, 1),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
            raise DatasetArchiveError("invalid_" + name)


def inspect_dataset_archive(
    path, *, max_members=_DEFAULT_MAX_MEMBERS,
    max_member_bytes=_DEFAULT_MAX_MEMBER_BYTES,
    max_total_bytes=_DEFAULT_MAX_TOTAL_BYTES,
):
    """Inspect ZIP or TAR metadata and return a bounded inventory; never extract."""
    _limits(max_members, max_member_bytes, max_total_bytes)
    if not os.path.isfile(path) or os.path.islink(path):
        raise DatasetArchiveError("archive_must_be_regular_non_symlink_file")
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
        if zipfile.is_zipfile(path):
            with zipfile.ZipFile(path) as archive:
                for item in archive.infolist():
                    mode = item.external_attr >> 16
                    file_type = stat.S_IFMT(mode)
                    if item.flag_bits & 0x1:
                        raise DatasetArchiveError("encrypted_archive_member_rejected")
                    if file_type == stat.S_IFLNK or (file_type not in (0, stat.S_IFREG, stat.S_IFDIR)):
                        raise DatasetArchiveError("unsupported_archive_member_type")
                    is_dir = item.is_dir()
                    add(item.filename, 0 if is_dir else item.file_size,
                        "directory" if is_dir else "file")
        else:
            with tarfile.open(path, mode="r:*") as archive:
                for item in archive:
                    if item.isdir():
                        kind = "directory"
                    elif item.isfile():
                        kind = "file"
                    else:
                        # Reject symlinks, hardlinks, devices, FIFOs and sparse variants.
                        raise DatasetArchiveError("unsupported_archive_member_type")
                    add(item.name, 0 if kind == "directory" else item.size, kind)
    except DatasetArchiveError:
        raise
    except (OSError, tarfile.TarError, zipfile.BadZipFile, EOFError) as exc:
        raise DatasetArchiveError("invalid_or_corrupt_archive") from exc

    return {"format": "zip" if zipfile.is_zipfile(path) else "tar",
            "member_count": len(entries), "total_uncompressed_bytes": total,
            "entries": entries}
