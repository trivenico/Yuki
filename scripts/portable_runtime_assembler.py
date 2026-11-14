"""Orchestrate verified, offline portable-runtime assembly.

This module binds archive digest verification, safe extraction, runtime Python
selection, and hash-locked offline dependency installation into one operation.
It never downloads inputs or runs the installed Yuki application.
"""
from __future__ import annotations

import shutil
from pathlib import Path, PurePosixPath

from scripts.portable_archive import extract_zip_safely
from scripts.portable_runtime_inputs import (
    InputValidationError,
    install_offline_dependencies,
    validate_runtime_archive,
)


def _runtime_python_path(runtime_root: Path, relative_path: str) -> Path:
    if not isinstance(relative_path, str) or not relative_path:
        raise InputValidationError("Runtime Python path must be a non-empty relative path.")
    if "\\" in relative_path or ":" in relative_path:
        raise InputValidationError("Runtime Python path must use portable relative separators.")
    parsed = PurePosixPath(relative_path)
    if parsed.is_absolute() or any(part in {"", ".", ".."} for part in relative_path.split("/")):
        raise InputValidationError("Runtime Python path must not be absolute or traverse directories.")
    candidate = runtime_root.joinpath(*parsed.parts)
    if candidate.is_symlink() or not candidate.is_file():
        raise InputValidationError("Selected runtime Python must be a regular, non-symlink file.")
    resolved = candidate.resolve(strict=True)
    try:
        resolved.relative_to(runtime_root.resolve(strict=True))
    except ValueError as exc:
        raise InputValidationError("Selected runtime Python resolves outside the extracted runtime.") from exc
    return resolved


def assemble_portable_runtime(
    archive: Path,
    expected_sha256: str,
    runtime_root: Path,
    python_relative_path: str,
    requirements_lock: Path,
    wheelhouse: Path,
    dependency_target: Path,
    *,
    timeout_seconds: int = 1800,
    runner=None,
) -> dict:
    """Verify and assemble one runtime from pinned, offline inputs.

    The archive digest is checked immediately before extraction. Dependencies
    are installed only by the selected interpreter inside that extracted tree.
    If dependency installation fails, the newly extracted runtime is removed.
    """
    archive = Path(archive).expanduser()
    runtime_root = Path(runtime_root).expanduser()
    dependency_target = Path(dependency_target).expanduser()
    if runtime_root.exists() or runtime_root.is_symlink():
        raise InputValidationError("Runtime destination must not already exist.")
    if dependency_target.exists() or dependency_target.is_symlink():
        raise InputValidationError("Dependency destination must not already exist.")
    if runtime_root.parent.resolve(strict=True) != dependency_target.parent.resolve(strict=True):
        # Keep publication on a single trusted filesystem and avoid cross-volume
        # assumptions in downstream deployment tooling.
        raise InputValidationError("Runtime and dependency destinations must share the same parent directory.")

    archive_report = validate_runtime_archive(archive, expected_sha256)
    extraction_report = extract_zip_safely(archive, runtime_root, expected_sha256=expected_sha256)
    try:
        interpreter = _runtime_python_path(runtime_root, python_relative_path)
        install_kwargs = {"timeout_seconds": timeout_seconds}
        if runner is not None:
            install_kwargs["runner"] = runner
        install_report = install_offline_dependencies(
            runtime_root,
            interpreter,
            requirements_lock,
            wheelhouse,
            dependency_target,
            **install_kwargs,
        )
    except Exception:
        shutil.rmtree(runtime_root, ignore_errors=True)
        raise

    return {
        "runtime_archive": archive_report,
        "runtime_extraction": extraction_report,
        "python_relative_path": python_relative_path,
        "dependencies": install_report,
        "network_downloads": False,
        "application_executed": False,
    }
