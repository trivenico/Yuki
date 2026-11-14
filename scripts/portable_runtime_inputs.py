#!/usr/bin/env python3
"""Validate immutable inputs for an offline portable-runtime build.

This gate deliberately does not download, unpack, install, or execute anything.
The builder must receive a pinned runtime archive and a platform-specific,
hash-locked wheelhouse prepared by a separate trusted process.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

_SHA256 = re.compile(r"^[a-fA-F0-9]{64}$")
_HASH_TOKEN = re.compile(r"--hash=sha256:([a-fA-F0-9]{64})")
_PINNED_REQUIREMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*(?:\[[A-Za-z0-9._,-]+\])?==[^\s;]+")


class InputValidationError(ValueError):
    """Raised when a portable build input is not sufficiently pinned."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _regular_file(path: Path, label: str) -> Path:
    if path.is_symlink() or not path.is_file():
        raise InputValidationError(f"{label} must be a regular, non-symlink file.")
    return path.resolve(strict=True)


def validate_runtime_archive(archive: Path, expected_sha256: str) -> dict:
    archive = _regular_file(archive, "Runtime archive")
    if not isinstance(expected_sha256, str) or not _SHA256.fullmatch(expected_sha256):
        raise InputValidationError("Expected runtime SHA-256 must be exactly 64 hexadecimal characters.")
    actual = sha256_file(archive)
    if actual.lower() != expected_sha256.lower():
        raise InputValidationError("Runtime archive SHA-256 does not match the pinned digest.")
    return {"path": archive.name, "size": archive.stat().st_size, "sha256": actual}


def _logical_requirement_lines(lock_path: Path) -> list[str]:
    raw = lock_path.read_text(encoding="utf-8")
    logical: list[str] = []
    pending = ""
    for raw_line in raw.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.endswith("\\"):
            pending += " " + line[:-1].strip()
            continue
        logical.append((pending + " " + line).strip())
        pending = ""
    if pending:
        raise InputValidationError("Lock file ends with an unfinished line continuation.")
    return logical


def validate_offline_wheelhouse(lock_file: Path, wheelhouse: Path) -> dict:
    lock_file = _regular_file(lock_file, "Requirements lock")
    if wheelhouse.is_symlink() or not wheelhouse.is_dir():
        raise InputValidationError("Wheelhouse must be a directory, not a symlink.")

    lines = _logical_requirement_lines(lock_file)
    if not lines:
        raise InputValidationError("Requirements lock is empty.")

    requirement_hashes: list[set[str]] = []
    for line in lines:
        if line.startswith(("-", "--")) or "://" in line or line.startswith((".", "/")):
            raise InputValidationError("Lock may contain only pinned package requirements, not pip options, URLs, or local paths.")
        if not _PINNED_REQUIREMENT.match(line):
            raise InputValidationError("Every dependency must use an exact == version pin.")
        hashes = {value.lower() for value in _HASH_TOKEN.findall(line)}
        if not hashes:
            raise InputValidationError("Every locked requirement must include at least one SHA-256 hash.")
        requirement_hashes.append(hashes)

    wheel_hashes: dict[str, str] = {}
    for path in sorted(wheelhouse.iterdir()):
        if path.is_symlink() or not path.is_file():
            raise InputValidationError("Wheelhouse may contain only regular wheel files.")
        if path.suffix != ".whl":
            raise InputValidationError("Offline wheelhouse must contain wheels only; source archives are not accepted.")
        digest = sha256_file(path)
        if digest in wheel_hashes:
            raise InputValidationError("Wheelhouse contains duplicate-content wheels.")
        wheel_hashes[digest] = path.name

    if not wheel_hashes:
        raise InputValidationError("Wheelhouse contains no wheels.")

    available = set(wheel_hashes)
    for index, hashes in enumerate(requirement_hashes, start=1):
        if not (hashes & available):
            raise InputValidationError(f"Locked requirement line {index} has no matching wheel in the wheelhouse.")

    referenced = set().union(*requirement_hashes)
    unreferenced = sorted(set(wheel_hashes) - referenced)
    if unreferenced:
        raise InputValidationError("Wheelhouse contains a wheel whose digest is absent from the lock.")

    return {
        "lock_file": lock_file.name,
        "requirement_count": len(lines),
        "wheel_count": len(wheel_hashes),
        "wheels": [{"filename": name, "sha256": digest} for digest, name in sorted(wheel_hashes.items(), key=lambda item: item[1])],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-archive", required=True, type=Path)
    parser.add_argument("--runtime-sha256", required=True)
    parser.add_argument("--requirements-lock", required=True, type=Path)
    parser.add_argument("--wheelhouse", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        report = {
            "runtime": validate_runtime_archive(args.runtime_archive, args.runtime_sha256),
            "dependencies": validate_offline_wheelhouse(args.requirements_lock, args.wheelhouse),
            "network_installation": False,
            "execution_performed": False,
        }
    except (OSError, UnicodeError, InputValidationError) as exc:
        print(f"Portable runtime input validation failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
