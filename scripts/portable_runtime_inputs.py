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
import os
import subprocess
import shutil
import tempfile
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
        tokens = line.split()
        if tokens and tokens[0].startswith("-"):
            raise InputValidationError("Standalone pip options are not permitted in the requirements lock.")
        if not tokens or not _PINNED_REQUIREMENT.fullmatch(tokens[0]):
            raise InputValidationError("Every dependency must use an exact == version pin.")
        if any(not _HASH_TOKEN.fullmatch(token) for token in tokens[1:]):
            raise InputValidationError("Lock lines may contain only SHA-256 hash options after the pinned requirement.")
        hashes = {token.split(":", 1)[1].lower() for token in tokens[1:]}
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


def validate_pip_bootstrap_wheel(pip_wheel: Path, expected_sha256: str, expected_version: str) -> dict:
    """Validate a pinned universal pip wheel for temporary zipimport bootstrap."""
    pip_wheel = _regular_file(Path(pip_wheel).expanduser(), "Pip bootstrap wheel")
    if not isinstance(expected_sha256, str) or not _SHA256.fullmatch(expected_sha256):
        raise InputValidationError("Expected pip wheel SHA-256 must be exactly 64 hexadecimal characters.")
    if sha256_file(pip_wheel).lower() != expected_sha256.lower():
        raise InputValidationError("Pip bootstrap wheel SHA-256 does not match the pinned digest.")
    if not isinstance(expected_version, str) or not re.fullmatch(r"[0-9]+(?:\.[0-9]+){1,3}", expected_version):
        raise InputValidationError("Expected pip version must be an exact numeric version.")
    if pip_wheel.name != f"pip-{expected_version}-py3-none-any.whl":
        raise InputValidationError("Pip bootstrap wheel must use the pinned universal py3-none-any filename.")
    try:
        import zipfile
        with zipfile.ZipFile(pip_wheel) as archive:
            names = set(archive.namelist())
            if "pip/__init__.py" not in names or "pip/_internal/cli/main.py" not in names:
                raise InputValidationError("Pip bootstrap wheel is missing required pip modules.")
            metadata_names = [name for name in names if name.endswith(".dist-info/METADATA")]
            if len(metadata_names) != 1:
                raise InputValidationError("Pip bootstrap wheel must contain exactly one distribution metadata file.")
            metadata = archive.read(metadata_names[0]).decode("utf-8")
    except (OSError, zipfile.BadZipFile, UnicodeError) as exc:
        raise InputValidationError(f"Pip bootstrap wheel is not a valid wheel archive: {exc}") from exc
    fields = {}
    for line in metadata.splitlines():
        if not line:
            break
        if ":" in line:
            key, value = line.split(":", 1)
            fields.setdefault(key.strip().lower(), value.strip())
    if fields.get("name", "").lower().replace("_", "-") != "pip" or fields.get("version") != expected_version:
        raise InputValidationError("Pip bootstrap wheel metadata does not match the pinned pip name and version.")
    return {"filename": pip_wheel.name, "sha256": expected_sha256.lower(), "version": expected_version}


def _snapshot_verified_pip_wheel(pip_wheel: Path, expected_sha256: str, expected_version: str, parent: Path) -> tuple[Path, Path, dict]:
    """Copy the pip wheel into a private snapshot while hashing the copied bytes."""
    source = _regular_file(Path(pip_wheel).expanduser(), "Pip bootstrap wheel")
    if not isinstance(expected_sha256, str) or not _SHA256.fullmatch(expected_sha256):
        raise InputValidationError("Expected pip wheel SHA-256 must be exactly 64 hexadecimal characters.")
    if not isinstance(expected_version, str) or not re.fullmatch(r"[0-9]+(?:\.[0-9]+){1,3}", expected_version):
        raise InputValidationError("Expected pip version must be an exact numeric version.")
    expected_name = f"pip-{expected_version}-py3-none-any.whl"
    if source.name != expected_name:
        raise InputValidationError("Pip bootstrap wheel must use the pinned universal py3-none-any filename.")
    snapshot_dir = Path(tempfile.mkdtemp(prefix=".yuki-pip-wheel-", dir=parent))
    snapshot = snapshot_dir / expected_name
    digest = hashlib.sha256()
    try:
        with source.open("rb") as incoming, snapshot.open("xb") as outgoing:
            if not __stat_is_regular(incoming):
                raise InputValidationError("Pip bootstrap wheel must be a regular file.")
            for chunk in iter(lambda: incoming.read(1024 * 1024), b""):
                digest.update(chunk)
                outgoing.write(chunk)
            outgoing.flush()
            os.fsync(outgoing.fileno())
        if digest.hexdigest().lower() != expected_sha256.lower():
            raise InputValidationError("Pip bootstrap wheel SHA-256 does not match the pinned digest.")
        report = validate_pip_bootstrap_wheel(snapshot, expected_sha256, expected_version)
        return snapshot_dir, snapshot, report
    except Exception:
        shutil.rmtree(snapshot_dir, ignore_errors=True)
        raise


def __stat_is_regular(stream) -> bool:
    import stat
    return stat.S_ISREG(os.fstat(stream.fileno()).st_mode)


def install_offline_dependencies(runtime_root: Path, python_executable: Path, lock_file: Path, wheelhouse: Path, target: Path, *, timeout_seconds: int = 1800, runner=subprocess.run, pip_wheel: Path | None = None, pip_sha256: str | None = None, pip_version: str | None = None) -> dict:
    """Install a fully hash-locked dependency closure transactionally, offline."""
    runtime_root = Path(runtime_root).expanduser()
    if runtime_root.is_symlink() or not runtime_root.is_dir():
        raise InputValidationError("Verified runtime root must be an existing non-symlink directory.")
    runtime_root = runtime_root.resolve(strict=True)
    python_executable = Path(python_executable).expanduser()
    if python_executable.is_symlink() or not python_executable.is_file():
        raise InputValidationError("Runtime Python must be a regular, non-symlink executable.")
    python_executable = python_executable.resolve(strict=True)
    try:
        python_executable.relative_to(runtime_root)
    except ValueError as exc:
        raise InputValidationError("Runtime Python must resolve inside the independently verified runtime root.") from exc
    if type(timeout_seconds) is not int or timeout_seconds <= 0:
        raise ValueError("Installation timeout must be a positive integer.")
    lock_file = _regular_file(Path(lock_file).expanduser(), "Requirements lock")
    wheelhouse = Path(wheelhouse).expanduser()
    report = validate_offline_wheelhouse(lock_file, wheelhouse)
    target = Path(target).expanduser()
    if target.exists() or target.is_symlink():
        raise InputValidationError("Dependency target must not already exist.")
    parent = target.parent.resolve(strict=True)
    pip_snapshot_dir = None
    if pip_wheel is not None:
        if pip_sha256 is None or pip_version is None:
            raise InputValidationError("Pip wheel, pinned SHA-256 and exact version must be supplied together.")
        pip_snapshot_dir, pip_wheel_path, pip_report = _snapshot_verified_pip_wheel(
            pip_wheel, pip_sha256, pip_version, parent
        )
        bootstrap_code = (
            "import sys; sys.path.insert(0, sys.argv.pop(1)); "
            "from pip._internal.cli.main import main; raise SystemExit(main(sys.argv[1:]))"
        )
        pip_prefix = [str(python_executable), "-I", "-c", bootstrap_code, str(pip_wheel_path)]
    else:
        if pip_sha256 is not None or pip_version is not None:
            raise InputValidationError("Pip wheel, pinned SHA-256 and exact version must be supplied together.")
        pip_report = None
        pip_prefix = [str(python_executable), "-I", "-m", "pip"]
    stage = None
    try:
        stage = Path(tempfile.mkdtemp(prefix=".yuki-dependencies-stage-", dir=parent))
        command = pip_prefix + [
            "install", "--isolated", "--no-index", "--find-links", str(wheelhouse.resolve(strict=True)),
            "--require-hashes", "--only-binary=:all:", "--disable-pip-version-check",
            "--no-input", "--no-warn-script-location", "--target", str(stage), "-r", str(lock_file),
        ]
    environment = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONNOUSERSITE": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PIP_CONFIG_FILE": os.devnull,
    }
    try:
        try:
            completed = runner(command, check=False, capture_output=True, text=True, timeout=timeout_seconds, env=environment, shell=False)
        except subprocess.TimeoutExpired as exc:
            raise InputValidationError("Offline dependency installation exceeded its time limit.") from exc
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "pip failed without diagnostic output")[-4000:]
            raise InputValidationError(f"Offline dependency installation failed: {detail}")
        if not stage.is_dir() or stage.is_symlink():
            raise InputValidationError("Installer removed or replaced its staging directory.")
        os.replace(stage, target)
    except Exception:
        if stage is not None and stage.exists():
            shutil.rmtree(stage, ignore_errors=True)
        raise
    finally:
        if pip_snapshot_dir is not None:
            shutil.rmtree(pip_snapshot_dir, ignore_errors=True)
    result = {"installed_target": str(target), "requirement_count": report["requirement_count"], "wheel_count": report["wheel_count"], "network_index_access": False, "hash_checking": True, "atomic_publish": True, "pip_bootstrap": "pinned-wheel" if pip_report else "runtime-provided"}
    if pip_report:
        result["pip"] = pip_report
    return result



def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-archive", required=True, type=Path)
    parser.add_argument("--runtime-sha256", required=True)
    parser.add_argument("--requirements-lock", required=True, type=Path)
    parser.add_argument("--wheelhouse", required=True, type=Path)
    parser.add_argument("--install-runtime-root", type=Path)
    parser.add_argument("--install-python", type=Path)
    parser.add_argument("--install-target", type=Path)
    parser.add_argument("--install-timeout", type=int, default=1800)
    parser.add_argument("--pip-bootstrap-wheel", type=Path)
    parser.add_argument("--pip-bootstrap-sha256")
    parser.add_argument("--pip-bootstrap-version")
    args = parser.parse_args(argv)
    try:
        install_args = (args.install_runtime_root, args.install_python, args.install_target)
        if any(install_args) and not all(install_args):
            raise InputValidationError("--install-runtime-root, --install-python and --install-target must be supplied together.")
        report = {
            "runtime": validate_runtime_archive(args.runtime_archive, args.runtime_sha256),
            "dependencies": validate_offline_wheelhouse(args.requirements_lock, args.wheelhouse),
            "network_installation": False,
            "execution_performed": False,
        }
        if args.install_python and args.install_target:
            pip_args = (args.pip_bootstrap_wheel, args.pip_bootstrap_sha256, args.pip_bootstrap_version)
            if any(pip_args) and not all(pip_args):
                raise InputValidationError("--pip-bootstrap-wheel, --pip-bootstrap-sha256 and --pip-bootstrap-version must be supplied together.")
            report["installation"] = install_offline_dependencies(args.install_runtime_root, args.install_python, args.requirements_lock, args.wheelhouse, args.install_target, timeout_seconds=args.install_timeout, pip_wheel=args.pip_bootstrap_wheel, pip_sha256=args.pip_bootstrap_sha256, pip_version=args.pip_bootstrap_version)
            report["execution_performed"] = True
    except (OSError, UnicodeError, InputValidationError) as exc:
        print(f"Portable runtime input validation failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
