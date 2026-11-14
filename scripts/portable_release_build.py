"""Build an unsigned, audited portable Yuki release candidate from pinned inputs.

This is a local build orchestrator, not a downloader or signer. Runtime archive,
pip bootstrap wheel, dependency lock, and wheelhouse must be supplied by a
trusted release process with independently reviewed SHA-256 pins.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

from scripts.portable_native_audit import NativeAuditError, audit_bundle
from scripts.portable_runtime_assembler import assemble_portable_runtime
from scripts.portable_bundle import assemble_bundle
from scripts.portable_manifest import write_unsigned_manifest
from scripts.portable_runtime_inputs import InputValidationError


def _host_audit_platform() -> str:
    if sys.platform.startswith("linux"):
        return "linux"
    if sys.platform == "darwin":
        return "macos"
    if os.name == "nt":
        return "windows"
    raise InputValidationError(f"Unsupported build host for native audit: {sys.platform}")


def build_release_candidate(
    *,
    runtime_archive: Path,
    runtime_sha256: str,
    python_relative_path: str,
    requirements_lock: Path,
    wheelhouse: Path,
    pip_wheel: Path,
    pip_sha256: str,
    pip_version: str,
    work_root: Path,
    output_root: Path,
    platform_tag: str,
    runtime_version: str,
) -> dict:
    """Assemble, activate, inventory, and audit one host-native candidate."""
    work_root = Path(work_root).expanduser()
    output_root = Path(output_root).expanduser()
    if work_root.is_symlink() or not work_root.is_dir():
        raise InputValidationError("Work root must be an existing non-symlink directory.")
    if output_root.exists() or output_root.is_symlink():
        raise InputValidationError("Output root must not already exist.")
    work_root = work_root.resolve(strict=True)
    output_parent = output_root.parent.resolve(strict=True)
    if output_parent == work_root or work_root in output_parent.parents or output_parent in work_root.parents:
        raise InputValidationError("Output parent and work root must be disjoint.")
    if not platform_tag.strip() or len(platform_tag) > 100:
        raise InputValidationError("Platform tag must be non-empty text of at most 100 characters.")
    requested_os = platform_tag.split("-", 1)[0].lower()
    host_os = _host_audit_platform()
    if requested_os != host_os:
        raise InputValidationError(
            f"Build host ({host_os}) must match target platform OS ({requested_os}); cross-platform native audits are not supported."
        )
    if not runtime_version.strip() or len(runtime_version) > 100:
        raise InputValidationError("Runtime version must be non-empty text of at most 100 characters.")

    inputs = work_root / "verified-inputs"
    inputs.mkdir(mode=0o700)
    runtime_root = inputs / "runtime"
    dependency_root = inputs / "site-packages"
    assembly = assemble_portable_runtime(
        runtime_archive=Path(runtime_archive),
        expected_sha256=runtime_sha256,
        runtime_root=runtime_root,
        python_relative_path=python_relative_path,
        requirements_lock=Path(requirements_lock),
        wheelhouse=Path(wheelhouse),
        dependency_target=dependency_root,
        pip_bootstrap_wheel=Path(pip_wheel),
        pip_bootstrap_sha256=pip_sha256,
        pip_bootstrap_version=pip_version,
    )

    output_root.parent.mkdir(parents=True, exist_ok=True)
    output_root.mkdir(mode=0o700)
    bundle = output_root / "yuki-portable"
    try:
        bundle_report = assemble_bundle(runtime_root, dependency_root, bundle)

        entry_source = Path(__file__).resolve().parents[1] / "portable_entry.py"
        if entry_source.is_symlink() or not entry_source.is_file():
            raise InputValidationError("Trusted portable_entry.py source is missing or is not a regular file.")
        entry_target = bundle / "portable_entry.py"
        shutil.copyfile(entry_source, entry_target)
        entry_target.chmod(0o755)

        manifest_report = write_unsigned_manifest(
            bundle, platform=platform_tag, runtime_version=runtime_version
        )
        audit_platform = _host_audit_platform()
        audit_report = audit_bundle(bundle, audit_platform)
        if audit_report["metadata_failures"]:
            raise NativeAuditError(
                f"Native metadata audit found {audit_report['metadata_failures']} release-blocking issue(s)."
            )
    except Exception:
        shutil.rmtree(output_root, ignore_errors=True)
        raise

    return {
        "candidate": str(bundle),
        "platform": platform_tag,
        "runtime_version": runtime_version,
        "runtime_assembly": assembly,
        "bundle_assembly": bundle_report,
        "entrypoint": "portable_entry.py",
        "manifest": manifest_report,
        "native_audit": audit_report,
        "signed": False,
        "release_ready": False,
        "release_blockers": [
            "Protected release signing and signature verification",
            "Authenticated host installer and USB launcher integration",
            "Clean-machine relocation and runtime health tests",
            "Platform-specific native dependency resolution verification",
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-archive", required=True, type=Path)
    parser.add_argument("--runtime-sha256", required=True)
    parser.add_argument("--python-relative-path", required=True)
    parser.add_argument("--requirements-lock", required=True, type=Path)
    parser.add_argument("--wheelhouse", required=True, type=Path)
    parser.add_argument("--pip-wheel", required=True, type=Path)
    parser.add_argument("--pip-sha256", required=True)
    parser.add_argument("--pip-version", required=True)
    parser.add_argument("--work-root", required=True, type=Path)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--platform-tag", required=True)
    parser.add_argument("--runtime-version", required=True)
    args = parser.parse_args(argv)
    try:
        report = build_release_candidate(
            runtime_archive=args.runtime_archive,
            runtime_sha256=args.runtime_sha256,
            python_relative_path=args.python_relative_path,
            requirements_lock=args.requirements_lock,
            wheelhouse=args.wheelhouse,
            pip_wheel=args.pip_wheel,
            pip_sha256=args.pip_sha256,
            pip_version=args.pip_version,
            work_root=args.work_root,
            output_root=args.output_root,
            platform_tag=args.platform_tag,
            runtime_version=args.runtime_version,
        )
    except (OSError, UnicodeError, ValueError, InputValidationError, NativeAuditError) as exc:
        print(f"Portable release candidate build failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
