"""Build and verify an unsigned integrity manifest for portable runtime bundles.

A SHA-256 manifest detects accidental or post-build file changes. It is not a
signature and does not authenticate who produced the bundle.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
from pathlib import Path, PurePosixPath
from typing import Any

MANIFEST_NAME = "runtime-manifest.json"
MAX_MANIFEST_BYTES = 1024 * 1024
_DIGEST = re.compile(r"^[a-f0-9]{64}$")


class RuntimeManifestError(RuntimeError):
    """Raised when a portable runtime manifest or bundle is invalid."""


def _hash_file(path: Path) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            size += len(chunk)
            digest.update(chunk)
    return size, digest.hexdigest()


def _inventory(root: Path) -> list[dict[str, Any]]:
    files: list[dict[str, Any]] = []
    for current, dirs, names in os.walk(root, followlinks=False):
        current_path = Path(current)
        for directory in list(dirs):
            candidate = current_path / directory
            if candidate.is_symlink():
                raise RuntimeManifestError(f"Runtime bundle contains a symbolic-link directory: {candidate.relative_to(root)}")
        for name in names:
            path = current_path / name
            relative = path.relative_to(root).as_posix()
            if relative == MANIFEST_NAME:
                continue
            if path.is_symlink() or not path.is_file():
                raise RuntimeManifestError(f"Runtime bundle contains a non-regular file: {relative}")
            size, digest = _hash_file(path)
            files.append({"path": relative, "size": size, "sha256": digest})
    files.sort(key=lambda item: item["path"])
    if not files:
        raise RuntimeManifestError("Runtime bundle contains no regular files.")
    return files


def create_manifest(root: str | Path, *, platform: str, runtime_version: str) -> dict[str, Any]:
    bundle = Path(root).expanduser().resolve(strict=True)
    if not bundle.is_dir():
        raise RuntimeManifestError("Runtime bundle path must be a directory.")
    if not isinstance(platform, str) or not platform.strip() or len(platform) > 100:
        raise ValueError("platform must be non-empty bounded text.")
    if not isinstance(runtime_version, str) or not runtime_version.strip() or len(runtime_version) > 100:
        raise ValueError("runtime_version must be non-empty bounded text.")
    return {
        "schema_version": 1,
        "product": "Yuki Portable Runtime",
        "artifact_kind": "unsigned-portable-runtime",
        "platform": platform.strip(),
        "runtime_version": runtime_version.strip(),
        "files": _inventory(bundle),
    }


def _validate_manifest(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, dict) or set(value) != {
        "schema_version", "product", "artifact_kind", "platform", "runtime_version", "files"
    }:
        raise RuntimeManifestError("Runtime manifest has an invalid schema.")
    if (
        value["schema_version"] != 1
        or value["product"] != "Yuki Portable Runtime"
        or value["artifact_kind"] != "unsigned-portable-runtime"
        or not isinstance(value["platform"], str) or not value["platform"]
        or not isinstance(value["runtime_version"], str) or not value["runtime_version"]
        or not isinstance(value["files"], list) or not value["files"]
    ):
        raise RuntimeManifestError("Runtime manifest metadata is invalid.")
    seen: set[str] = set()
    for item in value["files"]:
        if not isinstance(item, dict) or set(item) != {"path", "size", "sha256"}:
            raise RuntimeManifestError("Runtime manifest file entry is invalid.")
        relative = item["path"]
        if not isinstance(relative, str) or not relative or "\\" in relative:
            raise RuntimeManifestError("Runtime manifest path is invalid.")
        parsed = PurePosixPath(relative)
        if parsed.is_absolute() or any(part in {"", ".", ".."} for part in relative.split("/")):
            raise RuntimeManifestError("Runtime manifest path escapes the bundle.")
        if parsed.as_posix() != relative or relative == MANIFEST_NAME or relative in seen:
            raise RuntimeManifestError("Runtime manifest contains a duplicate or non-canonical path.")
        seen.add(relative)
        if type(item["size"]) is not int or item["size"] < 0:
            raise RuntimeManifestError("Runtime manifest file size is invalid.")
        if not isinstance(item["sha256"], str) or not _DIGEST.fullmatch(item["sha256"]):
            raise RuntimeManifestError("Runtime manifest SHA-256 digest is invalid.")
    if [item["path"] for item in value["files"]] != sorted(seen):
        raise RuntimeManifestError("Runtime manifest file entries must be sorted.")
    return value["files"]


def verify_manifest(root: str | Path, manifest_path: str | Path | None = None) -> dict[str, Any]:
    bundle = Path(root).expanduser().resolve(strict=True)
    if not bundle.is_dir():
        raise RuntimeManifestError("Runtime bundle path must be a directory.")
    manifest_file = Path(manifest_path) if manifest_path is not None else bundle / MANIFEST_NAME
    if manifest_file.is_symlink() or not manifest_file.is_file():
        raise RuntimeManifestError("Runtime manifest must be a regular file.")
    if manifest_file.stat().st_size > MAX_MANIFEST_BYTES:
        raise RuntimeManifestError("Runtime manifest exceeds the size limit.")
    try:
        value = json.loads(manifest_file.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeManifestError("Runtime manifest is unreadable or invalid JSON.") from exc
    expected = _validate_manifest(value)
    actual = _inventory(bundle)
    if actual != expected:
        raise RuntimeManifestError("Runtime bundle inventory or file hashes do not match the manifest.")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("create")
    build.add_argument("bundle", type=Path)
    build.add_argument("--platform", required=True)
    build.add_argument("--runtime-version", required=True)
    verify = sub.add_parser("verify")
    verify.add_argument("bundle", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "create":
            bundle = args.bundle.expanduser().resolve(strict=True)
            manifest = create_manifest(bundle, platform=args.platform, runtime_version=args.runtime_version)
            output = bundle / MANIFEST_NAME
            output.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\\n", encoding="utf-8")
            print(f"Created unsigned integrity manifest for {len(manifest['files'])} files: {output}")
        else:
            manifest = verify_manifest(args.bundle)
            print(f"Verified {len(manifest['files'])} runtime files for {manifest['platform']}.")
    except (OSError, ValueError, RuntimeManifestError) as exc:
        print(f"Portable runtime manifest check failed: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
