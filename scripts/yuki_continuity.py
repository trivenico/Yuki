#!/usr/bin/env python3
"""Portable, integrity-checked Yuki continuity snapshots (standard library only).

Archives contain private runtime state. They are not encrypted and must be
stored on encrypted media. Never upload them to GitHub.
"""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from zipfile import ZIP_DEFLATED, ZipFile, BadZipFile

SCHEMA_VERSION = 1
MAX_FILES = 100_000
MAX_FILE_BYTES = 256 * 1024 * 1024
MAX_TOTAL_BYTES = 2 * 1024 * 1024 * 1024
SECRET_NAMES = {
    ".env", "credentials.json", "token.json", "google_oauth_client.json",
    "google_token.json", "id_rsa", "id_ed25519",
}
SECRET_PATTERNS = (".env.*", "*.pem", "*.key", "*.p12", "*.pfx", "*.kdbx")
SECRET_DIRS = {".ssh", ".gnupg", "credentials", "secrets"}
EXCLUDED_DIRS = {".git", ".venv", "venv", "__pycache__", "node_modules"}
MANIFEST_NAME = "yuki-continuity-manifest.json"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_relative(name: str) -> PurePosixPath:
    if not isinstance(name, str) or not name or "\x00" in name or "\\" in name:
        raise ValueError("snapshot contains an invalid path")
    pure = PurePosixPath(name)
    if pure.is_absolute() or any(part in ("", ".", "..") for part in pure.parts):
        raise ValueError("snapshot contains an unsafe path")
    if pure.parts and re.match(r"^[a-zA-Z]:", pure.parts[0]):
        raise ValueError("snapshot contains a drive-qualified path")
    if pure.as_posix() != name or name == MANIFEST_NAME:
        raise ValueError("snapshot contains a non-canonical path")
    return pure


def is_secret(relative: Path) -> bool:
    parts = {part.casefold() for part in relative.parts}
    if parts & SECRET_DIRS:
        return True
    if parts & EXCLUDED_DIRS:
        return True
    name = relative.name.casefold()
    return name in SECRET_NAMES or any(fnmatch.fnmatch(name, pattern) for pattern in SECRET_PATTERNS)


def git_metadata(repository: Path) -> dict[str, str | None]:
    def run(*args: str) -> str | None:
        try:
            result = subprocess.run(
                ["git", "-C", str(repository), *args],
                check=True, capture_output=True, text=True, timeout=5,
            )
            return result.stdout.strip() or None
        except (OSError, subprocess.SubprocessError):
            return None
    # Do not capture origin URLs: HTTPS remotes may embed access tokens or
    # credentials in user-info/query components. A commit and branch are enough
    # to identify the source revision without copying remote authentication data.
    return {"commit": run("rev-parse", "HEAD"), "branch": run("branch", "--show-current")}


def collect_files(source: Path) -> list[tuple[Path, str]]:
    files: list[tuple[Path, str]] = []
    for path in sorted(source.rglob("*")):
        relative = path.relative_to(source)
        if path.is_symlink() or is_secret(relative) or not path.is_file():
            continue
        size = path.stat().st_size
        if size > MAX_FILE_BYTES:
            raise ValueError(f"snapshot file exceeds size limit: {relative.as_posix()}")
        files.append((path, relative.as_posix()))
        if len(files) > MAX_FILES:
            raise ValueError("snapshot contains too many files")
    if sum(path.stat().st_size for path, _ in files) > MAX_TOTAL_BYTES:
        raise ValueError("snapshot exceeds total uncompressed size limit")
    return files


def create_snapshot(source: Path, destination: Path, repository: Path) -> Path:
    source = source.expanduser().resolve()
    destination = destination.expanduser().resolve()
    if not source.is_dir():
        raise ValueError(f"snapshot source does not exist: {source}")
    if destination == source or source in destination.parents or destination in source.parents:
        raise ValueError("snapshot destination and source must be separate directories")
    destination.mkdir(parents=True, exist_ok=True)
    files = collect_files(source)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    target = destination / f"yuki-continuity-{stamp}.zip"
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "repository": git_metadata(repository),
        "source_label": source.name,
        "file_count": len(files),
        "files": {name: sha256_file(path) for path, name in files},
        "excluded": "symlinks, known credential/key files, VCS and dependency caches",
    }
    fd, temporary_name = tempfile.mkstemp(prefix=".yuki-continuity-", suffix=".tmp", dir=destination)
    os.close(fd)
    temporary = Path(temporary_name)
    try:
        with ZipFile(temporary, "w", compression=ZIP_DEFLATED) as archive:
            archive.writestr(MANIFEST_NAME, json.dumps(manifest, sort_keys=True, indent=2))
            for path, name in files:
                archive.write(path, f"data/{name}")
        verify_snapshot(temporary)
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return target


def read_manifest(archive: ZipFile) -> dict:
    try:
        manifest = json.loads(archive.read(MANIFEST_NAME))
    except (KeyError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError("snapshot manifest is missing or invalid") from exc
    if not isinstance(manifest, dict) or manifest.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported Yuki continuity snapshot schema")
    files = manifest.get("files")
    if not isinstance(files, dict) or manifest.get("file_count") != len(files):
        raise ValueError("snapshot file manifest is invalid")
    if len(files) > MAX_FILES:
        raise ValueError("snapshot contains too many files")
    total = 0
    for relative, digest in files.items():
        _safe_relative(relative)
        if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
            raise ValueError("snapshot contains an invalid file digest")
    return manifest


def verify_snapshot(snapshot: Path) -> dict:
    try:
        with ZipFile(snapshot, "r") as archive:
            manifest = read_manifest(archive)
            infos = archive.infolist()
            names = [item.filename for item in infos]
            if len(names) != len(set(names)):
                raise ValueError("snapshot contains duplicate archive entries")
            expected = {f"data/{name}" for name in manifest["files"]}
            if set(names) != expected | {MANIFEST_NAME}:
                raise ValueError("snapshot contains missing or unexpected archive entries")
            total = 0
            for relative, expected_hash in manifest["files"].items():
                info = archive.getinfo(f"data/{relative}")
                if info.file_size > MAX_FILE_BYTES:
                    raise ValueError("snapshot file exceeds size limit")
                total += info.file_size
                if total > MAX_TOTAL_BYTES:
                    raise ValueError("snapshot exceeds total uncompressed size limit")
                digest = hashlib.sha256(archive.read(info)).hexdigest()
                if digest != expected_hash:
                    raise ValueError(f"snapshot integrity check failed: {relative}")
            return manifest
    except BadZipFile as exc:
        raise ValueError("snapshot is not a valid ZIP archive") from exc


def restore_snapshot(snapshot: Path, destination: Path) -> dict:
    # Validate the entire archive before creating any restored user files.
    manifest = verify_snapshot(snapshot)
    destination = destination.expanduser().absolute()
    if destination.is_symlink():
        raise ValueError("restore destination cannot be a symlink")
    if destination.exists() and (not destination.is_dir() or any(destination.iterdir())):
        raise ValueError("restore destination must be absent or an empty directory")
    parent = destination.parent
    parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".yuki-restore-", dir=parent))
    try:
        with ZipFile(snapshot, "r") as archive:
            for relative in manifest["files"]:
                pure = _safe_relative(relative)
                target = staging.joinpath(*pure.parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(f"data/{relative}") as source, target.open("xb") as output:
                    while chunk := source.read(1024 * 1024):
                        output.write(chunk)
        # Re-check the staged files before making the restored directory visible.
        for relative, expected_hash in manifest["files"].items():
            if sha256_file(staging.joinpath(*_safe_relative(relative).parts)) != expected_hash:
                raise ValueError(f"staged restore integrity check failed: {relative}")
        if destination.exists():
            destination.rmdir()
        os.replace(staging, destination)
        return manifest
    finally:
        if staging.exists():
            import shutil
            shutil.rmtree(staging, ignore_errors=True)


def default_source(repository: Path) -> Path:
    configured = os.environ.get("YUKI_HOME", "").strip()
    return Path(configured).expanduser() if configured else repository / "YukiData"


def main(argv: list[str] | None = None) -> int:
    repository = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="Back up and restore Yuki's portable private state.")
    sub = parser.add_subparsers(dest="command", required=True)
    snapshot = sub.add_parser("snapshot", help="create a local integrity-checked snapshot")
    snapshot.add_argument("--source", type=Path, default=default_source(repository))
    snapshot.add_argument("--destination", type=Path, default=repository / "BACKUP" / "Continuity")
    verify = sub.add_parser("verify", help="verify a continuity archive")
    verify.add_argument("archive", type=Path)
    restore = sub.add_parser("restore", help="restore into a new or empty directory")
    restore.add_argument("archive", type=Path)
    restore.add_argument("--destination", type=Path, default=default_source(repository))
    args = parser.parse_args(argv)
    try:
        if args.command == "snapshot":
            result = create_snapshot(args.source, args.destination, repository)
            print(f"Continuity snapshot created and verified: {result}")
        elif args.command == "verify":
            manifest = verify_snapshot(args.archive)
            print(f"Verified {manifest['file_count']} files from {args.archive}")
        else:
            manifest = restore_snapshot(args.archive, args.destination)
            print(f"Restored {manifest['file_count']} files into {args.destination}")
            print("Reinstall dependencies and reconfigure credentials separately on this computer.")
        return 0
    except (OSError, ValueError) as exc:
        print(f"Yuki continuity error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
