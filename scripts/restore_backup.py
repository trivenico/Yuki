"""Validate and safely restore Yuki tar.gz / zip backups.

Restores only regular files and directories into a new destination. It rejects
path traversal, links, special files, duplicate paths and oversized archives.
File contents are written with owner-only permissions on POSIX systems.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import shutil
import stat
import tarfile
import tempfile
import zipfile

MAX_ENTRIES = 100_000
MAX_TOTAL_BYTES = 10 * 1024 * 1024 * 1024
MAX_FILE_BYTES = 2 * 1024 * 1024 * 1024


class BackupRestoreError(ValueError):
    """The backup is invalid or unsafe to restore."""


def _safe_parts(name: str) -> tuple[str, ...]:
    if not isinstance(name, str) or not name or "\x00" in name:
        raise BackupRestoreError("invalid_archive_path")
    normalized = name.replace("\\", "/")
    posix = PurePosixPath(normalized)
    windows = PureWindowsPath(name)
    if posix.is_absolute() or windows.is_absolute() or windows.drive:
        raise BackupRestoreError("absolute_archive_path")
    parts = tuple(part for part in posix.parts if part not in ("", "."))
    if not parts or any(part == ".." for part in parts):
        raise BackupRestoreError("archive_path_traversal")
    if any(":" in part for part in parts):
        raise BackupRestoreError("invalid_archive_path_component")
    return parts


def _ensure_limits(entries: int, total: int, size: int) -> None:
    if entries > MAX_ENTRIES:
        raise BackupRestoreError("too_many_archive_entries")
    if size < 0 or size > MAX_FILE_BYTES or total + size > MAX_TOTAL_BYTES:
        raise BackupRestoreError("archive_size_limit_exceeded")


def _write_member(root: Path, parts: tuple[str, ...], source, size: int) -> None:
    target = root.joinpath(*parts)
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if target.exists():
        raise BackupRestoreError("duplicate_archive_path")
    written = 0
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if os.name == "posix":
        flags |= getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(target, flags, 0o600)
    try:
        with os.fdopen(fd, "wb", closefd=False) as output:
            while True:
                chunk = source.read(1024 * 1024)
                if not chunk:
                    break
                written += len(chunk)
                if written > size or written > MAX_FILE_BYTES:
                    raise BackupRestoreError("archive_member_size_mismatch")
                output.write(chunk)
        if written != size:
            raise BackupRestoreError("archive_member_size_mismatch")
    finally:
        os.close(fd)


def _restore_tar(archive: Path, root: Path) -> None:
    seen: set[tuple[str, ...]] = set()
    total = entries = 0
    with tarfile.open(archive, mode="r:gz") as bundle:
        for member in bundle:
            entries += 1
            parts = _safe_parts(member.name)
            _ensure_limits(entries, total, member.size if member.isfile() else 0)
            if parts in seen:
                raise BackupRestoreError("duplicate_archive_path")
            seen.add(parts)
            target = root.joinpath(*parts)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True, mode=0o700)
                continue
            if not member.isfile():
                raise BackupRestoreError("archive_links_and_special_files_not_allowed")
            stream = bundle.extractfile(member)
            if stream is None:
                raise BackupRestoreError("archive_member_unreadable")
            with stream:
                _write_member(root, parts, stream, member.size)
            total += member.size


def _restore_zip(archive: Path, root: Path) -> None:
    seen: set[tuple[str, ...]] = set()
    total = entries = 0
    with zipfile.ZipFile(archive, "r") as bundle:
        for member in bundle.infolist():
            entries += 1
            parts = _safe_parts(member.filename)
            mode = (member.external_attr >> 16) & 0xFFFF
            file_type = stat.S_IFMT(mode)
            is_dir = member.is_dir()
            if file_type not in (0, stat.S_IFREG, stat.S_IFDIR):
                raise BackupRestoreError("archive_links_and_special_files_not_allowed")
            if (is_dir and file_type == stat.S_IFREG) or (not is_dir and file_type == stat.S_IFDIR):
                raise BackupRestoreError("archive_entry_type_mismatch")
            if parts in seen:
                raise BackupRestoreError("duplicate_archive_path")
            seen.add(parts)
            _ensure_limits(entries, total, member.file_size if not is_dir else 0)
            target = root.joinpath(*parts)
            if is_dir:
                target.mkdir(parents=True, exist_ok=True, mode=0o700)
                continue
            with bundle.open(member, "r") as stream:
                _write_member(root, parts, stream, member.file_size)
            total += member.file_size


def restore_backup(archive_path: str | Path, destination: str | Path) -> Path:
    archive = Path(archive_path).expanduser()
    if archive.is_symlink() or not archive.is_file():
        raise BackupRestoreError("backup_archive_must_be_regular_file")
    destination_path = Path(destination).expanduser()
    parent = destination_path.parent.resolve()
    parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    target = parent / destination_path.name
    if target.exists() or target.is_symlink():
        raise BackupRestoreError("restore_destination_must_not_exist")
    temp = Path(tempfile.mkdtemp(prefix=".yuki-restore-", dir=parent))
    try:
        if archive.name.lower().endswith(".tar.gz"):
            _restore_tar(archive, temp)
        elif archive.suffix.lower() == ".zip":
            _restore_zip(archive, temp)
        else:
            raise BackupRestoreError("unsupported_backup_format")
        if os.name == "posix":
            for directory, subdirs, _files in os.walk(temp):
                os.chmod(directory, 0o700)
                for name in subdirs:
                    os.chmod(Path(directory) / name, 0o700)
        os.replace(temp, target)
        return target
    except (OSError, tarfile.TarError, zipfile.BadZipFile) as exc:
        shutil.rmtree(temp, ignore_errors=True)
        raise BackupRestoreError("backup_restore_failed") from exc
    except BaseException:
        shutil.rmtree(temp, ignore_errors=True)
        raise


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Safely restore a Yuki backup into a new directory.")
    parser.add_argument("archive", help="yuki-data-*.tar.gz or yuki-data-*.zip")
    parser.add_argument("destination", help="new, non-existent restore directory")
    args = parser.parse_args(argv)
    try:
        restored = restore_backup(args.archive, args.destination)
    except BackupRestoreError as exc:
        parser.error(str(exc))
    print(f"Restore completed: {restored}")
    print("Review the restored data before pointing Yuki at it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
