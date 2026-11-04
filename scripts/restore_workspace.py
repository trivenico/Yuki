"""Safely restore a Yuki workspace archive into a new directory.

Supports the plaintext ZIP and gzip-compressed TAR archives produced by the
local backup scripts. This module never overwrites an existing destination.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import sqlite3
import stat
import sys
import tarfile
import tempfile
import unicodedata
import zipfile


_SQLITE_HEADER = b"SQLite format 3\x00"
_CHUNK_SIZE = 1024 * 1024
_MAX_ENTRIES = 100_000
_MAX_PATH_RECORDS = 200_000
_MAX_TOTAL_BYTES = 20 * 1024**3
_MAX_FILE_BYTES = 8 * 1024**3
_WINDOWS_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}


class RestoreError(RuntimeError):
    """Raised when an archive cannot be restored safely."""


def _safe_parts(raw_name: str) -> tuple[str, ...]:
    if not raw_name or "\x00" in raw_name or "\\" in raw_name:
        raise RestoreError("archive_contains_unsafe_path")
    name = raw_name[:-1] if raw_name.endswith("/") else raw_name
    if not name or name.startswith("/") or name.startswith("//"):
        raise RestoreError("archive_contains_unsafe_path")
    raw_parts = name.split("/")
    if any(part in ("", ".", "..") for part in raw_parts) or len(raw_parts) > 128:
        raise RestoreError("archive_contains_unsafe_path")
    if any(len(part.encode("utf-8")) > 255 for part in raw_parts) or len(name.encode("utf-8")) > 4096:
        raise RestoreError("archive_path_too_long")
    for part in raw_parts:
        if ":" in part or part.endswith((".", " ")):
            raise RestoreError("archive_contains_unsafe_path")
        if any(ord(char) < 32 or ord(char) == 127 for char in part):
            raise RestoreError("archive_contains_unsafe_path")
        device_base = part.split(".", 1)[0].casefold()
        if device_base in _WINDOWS_RESERVED:
            raise RestoreError("archive_contains_unsafe_path")
    return tuple(raw_parts)


def _canonical(parts: tuple[str, ...]) -> str:
    return "/".join(unicodedata.normalize("NFC", part).casefold() for part in parts)


def _register_path(
    entries: dict[str, tuple[tuple[str, ...], str, bool]],
    parts: tuple[str, ...],
    kind: str,
) -> None:
    for index in range(1, len(parts)):
        parent = parts[:index]
        key = _canonical(parent)
        previous = entries.get(key)
        if previous and previous[1] != "dir":
            raise RestoreError("archive_path_type_collision")
        if previous and previous[0] != parent:
            raise RestoreError("archive_contains_case_colliding_path")
        if not previous:
            if len(entries) >= _MAX_PATH_RECORDS:
                raise RestoreError("archive_path_count_limit_exceeded")
            entries[key] = (parent, "dir", False)

    key = _canonical(parts)
    previous = entries.get(key)
    if previous:
        if kind == "dir" and previous[1] == "dir" and not previous[2]:
            entries[key] = (parts, "dir", True)
            return
        raise RestoreError("archive_contains_duplicate_path")
    if len(entries) >= _MAX_PATH_RECORDS:
        raise RestoreError("archive_path_count_limit_exceeded")
    entries[key] = (parts, kind, True)


def _archive_layout(entries: dict[str, tuple[tuple[str, ...], str, bool]]) -> tuple[str | None, Path]:
    top = {value[0][0] for value in entries.values()}
    root_name = next(iter(top)) if len(top) == 1 else None
    if root_name is not None:
        root_entry = entries.get(_canonical((root_name,)))
        has_root_file = bool(root_entry and root_entry[1] == "file")
        if not has_root_file:
            return root_name, Path(root_name)
    return None, Path(".")


def _copy_stream(stream, target: Path, expected_size: int, total: list[int]) -> None:
    if expected_size < 0 or expected_size > _MAX_FILE_BYTES:
        raise RestoreError("archive_file_size_limit_exceeded")
    written = 0
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as output:
            while True:
                chunk = stream.read(_CHUNK_SIZE)
                if not chunk:
                    break
                written += len(chunk)
                total[0] += len(chunk)
                if written > expected_size or total[0] > _MAX_TOTAL_BYTES:
                    raise RestoreError("archive_uncompressed_size_limit_exceeded")
                output.write(chunk)
    except FileExistsError as exc:
        raise RestoreError("archive_contains_duplicate_path") from exc
    if written != expected_size:
        raise RestoreError("archive_entry_size_mismatch")


def _extract_zip(archive_path: Path, payload: Path) -> tuple[int, int, str | None]:
    try:
        archive = zipfile.ZipFile(archive_path, "r")
    except (OSError, zipfile.BadZipFile) as exc:
        raise RestoreError("invalid_zip_archive") from exc
    with archive:
        infos = archive.infolist()
        if not infos or len(infos) > _MAX_ENTRIES:
            raise RestoreError("archive_entry_count_invalid")
        entries: dict[str, tuple[tuple[str, ...], str, bool]] = {}
        normalized: list[tuple[zipfile.ZipInfo, tuple[str, ...], str]] = []
        declared_total = 0
        for info in infos:
            if info.flag_bits & 0x1:
                raise RestoreError("encrypted_zip_not_supported_by_this_restore_tool")
            parts = _safe_parts(info.filename)
            mode = info.external_attr >> 16
            file_type = stat.S_IFMT(mode)
            is_dir = info.is_dir()
            if stat.S_ISLNK(mode) or file_type not in (0, stat.S_IFREG, stat.S_IFDIR):
                raise RestoreError("archive_contains_link_or_special_file")
            if is_dir and file_type not in (0, stat.S_IFDIR):
                raise RestoreError("archive_entry_type_mismatch")
            if not is_dir and file_type == stat.S_IFDIR:
                raise RestoreError("archive_entry_type_mismatch")
            kind = "dir" if is_dir else "file"
            _register_path(entries, parts, kind)
            if not is_dir:
                if info.file_size > _MAX_FILE_BYTES:
                    raise RestoreError("archive_file_size_limit_exceeded")
                declared_total += info.file_size
                if declared_total > _MAX_TOTAL_BYTES:
                    raise RestoreError("archive_uncompressed_size_limit_exceeded")
            normalized.append((info, parts, kind))

        root_name, _ = _archive_layout(entries)
        total = [0]
        files = 0
        for info, parts, kind in normalized:
            relative = parts[1:] if root_name is not None else parts
            if not relative:
                payload.mkdir(parents=True, exist_ok=True)
                continue
            target = payload.joinpath(*relative)
            if kind == "dir":
                target.mkdir(parents=True, exist_ok=True, mode=0o700)
                continue
            with archive.open(info, "r") as source:
                _copy_stream(source, target, info.file_size, total)
            files += 1
        return files, total[0], root_name, None


def _extract_tar(archive_path: Path, payload: Path) -> tuple[int, int, str | None]:
    try:
        archive = tarfile.open(archive_path, mode="r:gz")
    except (OSError, tarfile.TarError) as exc:
        raise RestoreError("invalid_tar_archive") from exc
    with archive:
        members = archive.getmembers()
        if not members or len(members) > _MAX_ENTRIES:
            raise RestoreError("archive_entry_count_invalid")
        entries: dict[str, tuple[tuple[str, ...], str, bool]] = {}
        normalized: list[tuple[tarfile.TarInfo, tuple[str, ...], str]] = []
        declared_total = 0
        for member in members:
            raw = member.name[:-1] if member.name.endswith("/") else member.name
            parts = _safe_parts(raw)
            if member.isdir():
                kind = "dir"
            elif member.isfile() and not getattr(member, "sparse", None):
                kind = "file"
            else:
                raise RestoreError("archive_contains_link_or_special_file")
            _register_path(entries, parts, kind)
            if kind == "file":
                if member.size < 0 or member.size > _MAX_FILE_BYTES:
                    raise RestoreError("archive_file_size_limit_exceeded")
                declared_total += member.size
                if declared_total > _MAX_TOTAL_BYTES:
                    raise RestoreError("archive_uncompressed_size_limit_exceeded")
            normalized.append((member, parts, kind))

        root_name, _ = _archive_layout(entries)
        total = [0]
        files = 0
        for member, parts, kind in normalized:
            relative = parts[1:] if root_name is not None else parts
            if not relative:
                payload.mkdir(parents=True, exist_ok=True)
                continue
            target = payload.joinpath(*relative)
            if kind == "dir":
                target.mkdir(parents=True, exist_ok=True)
                continue
            source = archive.extractfile(member)
            if source is None:
                raise RestoreError("archive_entry_unreadable")
            with source:
                _copy_stream(source, target, member.size, total)
            files += 1
        return files, total[0]


def _validate_sqlite_files(root: Path) -> int:
    count = 0
    for current, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames.sort()
        for name in filenames:
            if name.lower().endswith(("-wal", "-shm", "-journal")):
                raise RestoreError("archive_contains_sqlite_journal_sidecar")
            path = Path(current) / name
            try:
                with path.open("rb") as handle:
                    is_sqlite = handle.read(len(_SQLITE_HEADER)) == _SQLITE_HEADER
            except OSError as exc:
                raise RestoreError("restored_file_unreadable") from exc
            if not is_sqlite:
                continue
            connection = None
            try:
                connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
                result = connection.execute("PRAGMA integrity_check").fetchone()
                if not result or result[0] != "ok":
                    raise RestoreError("restored_sqlite_integrity_check_failed")
            except (OSError, sqlite3.Error) as exc:
                raise RestoreError("restored_sqlite_integrity_check_failed") from exc
            finally:
                if connection is not None:
                    connection.close()
            count += 1
    return count


def restore_workspace(archive_file: str | Path, destination_dir: str | Path) -> dict[str, int]:
    """Restore a supported archive to a new directory without overwriting data."""
    raw_archive = Path(archive_file).expanduser()
    if raw_archive.is_symlink():
        raise RestoreError("restore_archive_must_not_be_symlink")
    archive_path = raw_archive.resolve(strict=True)
    if not archive_path.is_file():
        raise RestoreError("restore_archive_must_be_file")
    raw_destination = Path(destination_dir).expanduser()
    if raw_destination.is_symlink():
        raise RestoreError("restore_destination_must_not_be_symlink")
    destination = raw_destination.resolve(strict=False)
    if destination.exists():
        raise RestoreError("restore_destination_already_exists")
    if archive_path.stat().st_size > _MAX_TOTAL_BYTES + 100 * 1024 * 1024:
        raise RestoreError("archive_compressed_size_limit_exceeded")
    parent = destination.parent
    if not parent.is_dir():
        raise RestoreError("restore_destination_parent_missing")
    if destination == archive_path or destination in archive_path.parents:
        raise RestoreError("restore_destination_contains_archive")

    staging = Path(tempfile.mkdtemp(prefix=".yuki-restore-", dir=str(parent)))
    payload = staging / "payload"
    payload.mkdir(mode=0o700)
    try:
        if archive_path.name.lower().endswith(".zip"):
            files, total, archive_root = _extract_zip(archive_path, payload)
        elif archive_path.name.lower().endswith((".tar.gz", ".tgz")):
            files, total, archive_root = _extract_tar(archive_path, payload)
        else:
            raise RestoreError("unsupported_archive_format")

        # Unix tar archives explicitly include the workspace root directory.
        # Windows ZIP backups omit it; never infer a wrapper from directory count,
        # because a valid workspace may contain only one nested directory.
        workspace = payload / archive_root if archive_root is not None else payload
        if not workspace.is_dir():
            raise RestoreError("archive_workspace_root_missing")
        databases = _validate_sqlite_files(workspace)
        if os.name != "nt":
            os.chmod(workspace, 0o700)
            for current, dirnames, filenames in os.walk(workspace):
                os.chmod(current, 0o700)
                for filename in filenames:
                    os.chmod(Path(current) / filename, 0o600)
        if destination.exists() or destination.is_symlink():
            raise RestoreError("restore_destination_already_exists")
        os.rename(workspace, destination)
        return {"files_restored": files, "bytes_restored": total, "sqlite_databases_verified": databases}
    except RestoreError:
        raise
    except (OSError, tarfile.TarError, zipfile.BadZipFile) as exc:
        raise RestoreError("workspace_restore_failed") from exc
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", help="Yuki .zip or .tar.gz backup archive")
    parser.add_argument("destination", help="New, empty directory for restored workspace")
    args = parser.parse_args(argv)
    try:
        result = restore_workspace(args.archive, args.destination)
    except (OSError, RestoreError) as exc:
        print(f"Workspace restore failed: {exc}", file=sys.stderr)
        return 1
    print(
        "Workspace restore complete: "
        f"{result['files_restored']} file(s), "
        f"{result['sqlite_databases_verified']} SQLite database(s) verified."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
