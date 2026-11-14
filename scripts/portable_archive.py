"""Safely extract bounded ZIP archives for portable-runtime assembly.

Only regular files and directories are accepted. Extraction is staged in a
fresh sibling directory and atomically published after every member succeeds.
This is a build-time utility; it does not execute extracted content.
"""
from __future__ import annotations

import argparse
import hashlib
import re
import os
import shutil
import stat
import sys
import tempfile
import unicodedata
import zipfile
from pathlib import Path, PurePosixPath

DEFAULT_MAX_FILES = 100_000
DEFAULT_MAX_TOTAL_BYTES = 4 * 1024 * 1024 * 1024
DEFAULT_MAX_FILE_BYTES = 2 * 1024 * 1024 * 1024
DEFAULT_MAX_COMPRESSION_RATIO = 1_000
_SHA256 = re.compile(r"^[a-fA-F0-9]{64}$")


class ArchiveValidationError(ValueError):
    """Raised when an archive member is unsafe or exceeds configured limits."""


def _portable_member_name(raw_name: str) -> tuple[str, bool]:
    if not raw_name or "\x00" in raw_name or "\\" in raw_name:
        raise ArchiveValidationError("Archive contains an empty or non-portable member path.")
    is_dir = raw_name.endswith("/")
    name = raw_name[:-1] if is_dir else raw_name
    path = PurePosixPath(name)
    parts = name.split("/")
    if path.is_absolute() or not parts or any(part in {"", ".", ".."} for part in parts):
        raise ArchiveValidationError("Archive member path is absolute or traverses directories.")
    if parts[0].endswith(":") or any(":" in part for part in parts):
        raise ArchiveValidationError("Archive member path contains a drive or alternate-stream separator.")
    reserved = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
    for part in parts:
        if (part.endswith((" ", ".")) or any(ord(ch) < 32 or ch in '<>:"|?*' for ch in part)
                or part.split(".", 1)[0].upper() in reserved):
            raise ArchiveValidationError("Archive member path is not portable across supported operating systems.")
    canonical = "/".join(parts)
    return canonical, is_dir


def _member_kind(info: zipfile.ZipInfo, is_dir: bool) -> int:
    mode = (info.external_attr >> 16) & 0xFFFF
    kind = stat.S_IFMT(mode)
    if info.flag_bits & 0x1:
        raise ArchiveValidationError("Encrypted ZIP members are not supported.")
    if kind == stat.S_IFLNK:
        raise ArchiveValidationError("Symbolic links are not accepted in runtime archives.")
    if kind not in (0, stat.S_IFREG, stat.S_IFDIR):
        raise ArchiveValidationError("Special files are not accepted in runtime archives.")
    if is_dir and kind == stat.S_IFREG:
        raise ArchiveValidationError("ZIP directory entry has a regular-file mode.")
    if not is_dir and kind == stat.S_IFDIR:
        raise ArchiveValidationError("ZIP file entry has a directory mode.")
    return mode


def extract_zip_safely(
    archive: str | Path,
    destination: str | Path,
    *,
    max_files: int = DEFAULT_MAX_FILES,
    max_total_bytes: int = DEFAULT_MAX_TOTAL_BYTES,
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
    max_compression_ratio: int = DEFAULT_MAX_COMPRESSION_RATIO,
    expected_sha256: str | None = None,
) -> dict:
    archive_path = Path(archive).expanduser()
    target = Path(destination).expanduser()
    if archive_path.is_symlink() or not archive_path.is_file():
        raise ArchiveValidationError("Runtime archive must be a regular non-symlink file.")
    if any(type(value) is not int or value <= 0 for value in
           (max_files, max_total_bytes, max_file_bytes, max_compression_ratio)):
        raise ValueError("Extraction limits must be positive integers.")
    parent = target.parent.resolve(strict=True)
    if target.exists() or target.is_symlink():
        raise ArchiveValidationError("Extraction destination must not already exist.")
    if not parent.is_dir() or target.name in {"", ".", ".."}:
        raise ArchiveValidationError("Extraction destination parent is invalid.")

    verified_archive = archive_path
    snapshot_path: Path | None = None
    if expected_sha256 is not None:
        if not isinstance(expected_sha256, str) or not _SHA256.fullmatch(expected_sha256):
            raise ArchiveValidationError("Expected archive SHA-256 must be exactly 64 hexadecimal characters.")
        descriptor, snapshot_name = tempfile.mkstemp(prefix=".yuki-archive-verified-", dir=parent)
        snapshot_path = Path(snapshot_name)
        digest = hashlib.sha256()
        try:
            with os.fdopen(descriptor, "wb") as snapshot, archive_path.open("rb") as source:
                if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                    raise ArchiveValidationError("Runtime archive must resolve to a regular file.")
                while True:
                    chunk = source.read(1024 * 1024)
                    if not chunk:
                        break
                    digest.update(chunk)
                    snapshot.write(chunk)
                snapshot.flush()
                os.fsync(snapshot.fileno())
            if digest.hexdigest().lower() != expected_sha256.lower():
                raise ArchiveValidationError("Runtime archive SHA-256 does not match the pinned digest.")
        except Exception:
            snapshot_path.unlink(missing_ok=True)
            raise
        verified_archive = snapshot_path

    try:
        zf = zipfile.ZipFile(verified_archive, "r")
    except (OSError, zipfile.BadZipFile) as exc:
        if snapshot_path is not None:
            snapshot_path.unlink(missing_ok=True)
        raise ArchiveValidationError("Runtime archive is not a readable ZIP file.") from exc

    stage = Path(tempfile.mkdtemp(prefix=".yuki-runtime-stage-", dir=parent))
    count = 0
    total = 0
    seen: dict[str, str] = {}
    file_paths: set[str] = set()
    dir_paths: set[str] = set()
    try:
        with zf:
            infos = zf.infolist()
            if len(infos) > max_files:
                raise ArchiveValidationError("Runtime archive exceeds the member-count limit.")
            for info in infos:
                name, is_dir = _portable_member_name(info.orig_filename)
                mode = _member_kind(info, is_dir)
                key = unicodedata.normalize("NFC", name).casefold()
                if key in seen:
                    raise ArchiveValidationError("Runtime archive contains duplicate or case-colliding paths.")
                seen[key] = name
                parts = name.split("/")
                for index in range(1, len(parts)):
                    parent_name = "/".join(parts[:index])
                    if parent_name in file_paths:
                        raise ArchiveValidationError("Runtime archive has a file used as a parent directory.")
                    dir_paths.add(parent_name)
                if is_dir:
                    if name in file_paths:
                        raise ArchiveValidationError("Runtime archive path is both a file and directory.")
                    dir_paths.add(name)
                    continue
                if name in dir_paths:
                    raise ArchiveValidationError("Runtime archive path is both a file and directory.")
                file_paths.add(name)
                count += 1
                if info.file_size < 0 or info.file_size > max_file_bytes:
                    raise ArchiveValidationError("Runtime archive member exceeds the per-file size limit.")
                total += info.file_size
                if total > max_total_bytes:
                    raise ArchiveValidationError("Runtime archive exceeds the total expanded-size limit.")
                if info.file_size and (info.compress_size == 0 or
                        info.file_size > max_compression_ratio * info.compress_size):
                    raise ArchiveValidationError("Runtime archive member exceeds the compression-ratio limit.")

                output = stage.joinpath(*parts)
                output.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info, "r") as source, output.open("xb") as sink:
                    copied = 0
                    while True:
                        chunk = source.read(1024 * 1024)
                        if not chunk:
                            break
                        copied += len(chunk)
                        if copied > info.file_size or copied > max_file_bytes:
                            raise ArchiveValidationError("Runtime archive member expanded beyond its declared size.")
                        sink.write(chunk)
                    if copied != info.file_size:
                        raise ArchiveValidationError("Runtime archive member size does not match its directory entry.")
                permissions = (mode & 0o777) if mode else 0o644
                permissions &= 0o755
                os.chmod(output, permissions or 0o644)

        if count == 0:
            raise ArchiveValidationError("Runtime archive contains no regular files.")
        os.replace(stage, target)
        return {"files": count, "expanded_bytes": total, "destination": str(target)}
    except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
        if isinstance(exc, ArchiveValidationError):
            raise
        raise ArchiveValidationError("Runtime archive extraction failed.") from exc
    finally:
        if stage.exists():
            shutil.rmtree(stage, ignore_errors=True)
        if snapshot_path is not None:
            snapshot_path.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", required=True, type=Path)
    parser.add_argument("--destination", required=True, type=Path)
    parser.add_argument("--max-files", type=int, default=DEFAULT_MAX_FILES)
    parser.add_argument("--max-total-bytes", type=int, default=DEFAULT_MAX_TOTAL_BYTES)
    parser.add_argument("--max-file-bytes", type=int, default=DEFAULT_MAX_FILE_BYTES)
    parser.add_argument("--max-compression-ratio", type=int, default=DEFAULT_MAX_COMPRESSION_RATIO)
    args = parser.parse_args(argv)
    try:
        report = extract_zip_safely(
            args.archive, args.destination, max_files=args.max_files,
            max_total_bytes=args.max_total_bytes, max_file_bytes=args.max_file_bytes,
            max_compression_ratio=args.max_compression_ratio,
        )
    except (OSError, ValueError, ArchiveValidationError) as exc:
        print(f"Portable archive extraction failed: {exc}", file=sys.stderr)
        return 2
    import json
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
