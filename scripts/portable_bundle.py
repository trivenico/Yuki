"""Assemble verified runtime and dependency trees into a portable bundle layout.

This build-time step copies inputs without executing them. It deliberately
keeps the interpreter and site-packages in separate, predictable directories;
platform launchers and final signing are separate release steps.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import sys
import tempfile
from pathlib import Path

from scripts.portable_runtime_inputs import InputValidationError


def _source_directory(value: Path, label: str) -> Path:
    value = Path(value).expanduser()
    if value.is_symlink() or not value.is_dir():
        raise InputValidationError(f"{label} must be an existing non-symlink directory.")
    return value.resolve(strict=True)


def _assert_disjoint(source: Path, destination_parent: Path) -> None:
    if source == destination_parent or source in destination_parent.parents or destination_parent in source.parents:
        raise InputValidationError("Bundle destination parent and source trees must not overlap.")


def _copy_tree(source: Path, destination: Path) -> int:
    count = 0
    destination.mkdir(parents=True, exist_ok=False)
    for current, dirs, files in os.walk(source, topdown=True, followlinks=False):
        current_path = Path(current)
        relative_dir = current_path.relative_to(source)
        target_dir = destination / relative_dir
        for name in list(dirs):
            item = current_path / name
            if item.is_symlink() or not item.is_dir():
                raise InputValidationError(f"Input tree contains a symbolic-link or invalid directory: {item.relative_to(source)}")
            (target_dir / name).mkdir(exist_ok=True)
        for name in files:
            item = current_path / name
            info = item.lstat()
            if not stat.S_ISREG(info.st_mode):
                raise InputValidationError(f"Input tree contains a non-regular file: {item.relative_to(source)}")
            target = target_dir / name
            if target.exists() or target.is_symlink():
                raise InputValidationError(f"Input tree contains a duplicate destination path: {target.relative_to(destination)}")
            shutil.copyfile(item, target, follow_symlinks=False)
            target.chmod(stat.S_IMODE(info.st_mode) & 0o777)
            count += 1
    return count


def assemble_bundle(runtime_root: Path, dependency_root: Path, destination: Path) -> dict:
    runtime = _source_directory(runtime_root, "Runtime root")
    dependencies = _source_directory(dependency_root, "Dependency root")
    destination = Path(destination).expanduser()
    parent = destination.parent.resolve(strict=True)
    if destination.exists() or destination.is_symlink():
        raise InputValidationError("Bundle destination must not already exist.")
    if runtime == dependencies or runtime in dependencies.parents or dependencies in runtime.parents:
        raise InputValidationError("Runtime and dependency source trees must be separate.")
    _assert_disjoint(runtime, parent)
    _assert_disjoint(dependencies, parent)

    stage = Path(tempfile.mkdtemp(prefix=".yuki-bundle-stage-", dir=parent))
    try:
        runtime_count = _copy_tree(runtime, stage / "runtime")
        dependency_count = _copy_tree(dependencies, stage / "site-packages")
        if runtime_count == 0 or dependency_count == 0:
            raise InputValidationError("Runtime and dependency inputs must both contain files.")
        os.replace(stage, destination)
    except Exception:
        shutil.rmtree(stage, ignore_errors=True)
        raise
    return {
        "bundle": str(destination),
        "runtime_files": runtime_count,
        "dependency_files": dependency_count,
        "total_files": runtime_count + dependency_count,
        "atomic_publish": True,
        "application_executed": False,
        "signed": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-root", required=True, type=Path)
    parser.add_argument("--dependency-root", required=True, type=Path)
    parser.add_argument("--destination", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        report = assemble_bundle(args.runtime_root, args.dependency_root, args.destination)
    except (OSError, ValueError, InputValidationError) as exc:
        print(f"Portable bundle assembly failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
