#!/usr/bin/env python3
"""Build a target-specific offline wheelhouse from a reviewed hash lock.

This is a networked build-time operation. It downloads binary wheels only,
uses exact hashes from the lock, validates the complete output, and publishes
the wheelhouse atomically. It never installs or executes downloaded packages.
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

from scripts.portable_runtime_inputs import (
    InputValidationError,
    _regular_file,
    validate_offline_wheelhouse,
)

_TOKEN = re.compile(r"^[A-Za-z0-9_.-]+$")
_EXACT_PIN = re.compile(r"^(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)(?:\[A-Za-z0-9._,-]+\])?==(?P<version>[A-Za-z0-9][A-Za-z0-9.!+_-]*)$")


def _validate_lock(lock_file: Path) -> None:
    """Validate lock syntax before invoking pip, without needing wheel files."""
    from scripts.portable_runtime_inputs import (
        _HASH_TOKEN, _logical_requirement_lines,
    )

    lines = _logical_requirement_lines(_regular_file(lock_file, "Requirements lock"))
    if not lines:
        raise InputValidationError("Requirements lock is empty.")
    has_yuki = False
    for number, line in enumerate(lines, start=1):
        tokens = line.split()
        match = _EXACT_PIN.fullmatch(tokens[0]) if tokens else None
        if not match:
            raise InputValidationError(
                f"Lock line {number} must be an exact version pin without environment markers or options."
            )
        if any(not _HASH_TOKEN.fullmatch(token) for token in tokens[1:]) or not tokens[1:]:
            raise InputValidationError(f"Lock line {number} must contain only SHA-256 hashes.")
        normalized_name = match.group("name").lower().replace("_", "-").replace(".", "-")
        if normalized_name == "yuki-ai-assistant":
            has_yuki = True
    if not has_yuki:
        raise InputValidationError("Portable lock must include an exact yuki-ai-assistant application wheel pin.")


def _validate_index_url(value: str | None) -> str | None:
    if value is None:
        return None
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise InputValidationError("Index URL must be HTTPS and must not embed credentials.")
    if parsed.fragment:
        raise InputValidationError("Index URL must not contain a fragment.")
    return value


def build_wheelhouse(
    lock_file: Path,
    destination: Path,
    *,
    platform: str,
    python_version: str,
    implementation: str,
    abi: str,
    index_url: str | None = None,
    timeout_seconds: int = 1800,
    runner=subprocess.run,
) -> dict:
    lock_file = _regular_file(Path(lock_file).expanduser(), "Requirements lock")
    destination = Path(destination).expanduser()
    if destination.exists() or destination.is_symlink():
        raise InputValidationError("Wheelhouse destination must not already exist.")
    parent = destination.parent.resolve(strict=True)
    if not destination.name or destination.name in {".", ".."}:
        raise InputValidationError("Wheelhouse destination name is invalid.")
    for label, value in (("platform", platform), ("Python version", python_version),
                         ("implementation", implementation), ("ABI", abi)):
        if not isinstance(value, str) or not _TOKEN.fullmatch(value):
            raise InputValidationError(f"Target {label} contains unsupported characters.")
    if not re.fullmatch(r"[0-9]+\.[0-9]+", python_version):
        raise InputValidationError("Python version must use MAJOR.MINOR form, such as 3.12.")
    if type(timeout_seconds) is not int or timeout_seconds <= 0:
        raise ValueError("Download timeout must be a positive integer.")
    index_url = _validate_index_url(index_url)
    _validate_lock(lock_file)

    stage = Path(tempfile.mkdtemp(prefix=".yuki-wheelhouse-stage-", dir=parent))
    command = [
        sys.executable, "-I", "-m", "pip", "download", "--isolated",
        "--require-hashes", "--only-binary=:all:", "--no-deps",
        "--disable-pip-version-check", "--no-input",
        "--platform", platform, "--python-version", python_version,
        "--implementation", implementation, "--abi", abi,
        "--dest", str(stage), "--requirement", str(lock_file),
    ]
    if index_url:
        command.extend(["--index-url", index_url])
    environment = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONNOUSERSITE": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PIP_CONFIG_FILE": os.devnull,
    }
    for key in ("SYSTEMROOT", "WINDIR", "TEMP", "TMP"):
        if os.environ.get(key):
            environment[key] = os.environ[key]
    try:
        try:
            completed = runner(
                command, check=False, capture_output=True, text=True,
                timeout=timeout_seconds, env=environment, shell=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise InputValidationError("Wheelhouse download exceeded its time limit.") from exc
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "pip download failed")[-4000:]
            raise InputValidationError(f"Wheelhouse download failed: {detail}")
        report = validate_offline_wheelhouse(lock_file, stage)
        os.replace(stage, destination)
    except Exception:
        if stage.exists():
            shutil.rmtree(stage, ignore_errors=True)
        raise
    return {
        "destination": str(destination),
        "platform": platform,
        "python_version": python_version,
        "implementation": implementation,
        "abi": abi,
        "wheel_count": report["wheel_count"],
        "requirement_count": report["requirement_count"],
        "hash_checking": True,
        "binary_wheels_only": True,
        "dependency_resolution": False,
        "network_access": True,
        "atomic_publish": True,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lock", required=True, type=Path)
    parser.add_argument("--destination", required=True, type=Path)
    parser.add_argument("--platform", required=True)
    parser.add_argument("--python-version", required=True)
    parser.add_argument("--implementation", default="cp")
    parser.add_argument("--abi", required=True)
    parser.add_argument("--index-url")
    parser.add_argument("--timeout", type=int, default=1800)
    args = parser.parse_args(argv)
    try:
        report = build_wheelhouse(
            args.lock, args.destination, platform=args.platform,
            python_version=args.python_version, implementation=args.implementation,
            abi=args.abi, index_url=args.index_url, timeout_seconds=args.timeout,
        )
    except (OSError, UnicodeError, InputValidationError, ValueError) as exc:
        print(f"Portable wheelhouse build failed: {exc}", file=sys.stderr)
        return 2
    import json
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
