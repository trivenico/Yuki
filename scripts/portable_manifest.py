#!/usr/bin/env python3
"""Create a deterministic, unsigned inventory manifest for a portable bundle.

Signing is deliberately separate: release signing must use a protected key
that is never stored in the bundle or repository.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import sys
import tempfile
import unicodedata
from pathlib import Path

from scripts.portable_runtime_inputs import InputValidationError

MANIFEST_NAME = "runtime-manifest.json"
SIGNATURE_NAME = "runtime-manifest.sig"
_WINDOWS_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def _validate_relative_path(relative: str) -> None:
    parts = relative.split("/")
    if not relative or "\\\\" in relative or any(part in {"", ".", ".."} for part in parts):
        raise InputValidationError("Bundle contains a non-canonical relative path.")
    for part in parts:
        if (
            part.endswith((" ", "."))
            or any(ord(char) < 32 or char in '<>:"|?*' for char in part)
            or part.split(".", 1)[0].rstrip(" .").upper() in _WINDOWS_RESERVED
        ):
            raise InputValidationError(f"Bundle path is not portable across operating systems: {relative}")


def inventory_bundle(root: Path) -> list[dict]:
    root = Path(root).expanduser()
    if root.is_symlink() or not root.is_dir():
        raise InputValidationError("Bundle root must be an existing, non-symlink directory.")
    root = root.resolve(strict=True)
    entries: list[dict] = []
    seen: set[str] = set()
    portable: set[str] = set()
    nodes: dict[str, tuple[str, bool]] = {}
    for current, dirs, names in os.walk(root, topdown=True, followlinks=False):
        current_path = Path(current)
        for name in list(dirs):
            path = current_path / name
            if path.is_symlink() or not path.is_dir():
                raise InputValidationError(f"Bundle contains a symbolic-link or invalid directory: {path.relative_to(root)}")
        for name in names:
            path = current_path / name
            relative = path.relative_to(root).as_posix()
            _validate_relative_path(relative)
            if relative in {MANIFEST_NAME, SIGNATURE_NAME}:
                raise InputValidationError("Refusing to overwrite an existing manifest or signature.")
            info = path.lstat()
            if not stat.S_ISREG(info.st_mode):
                raise InputValidationError(f"Bundle contains a non-regular file: {relative}")
            parts = relative.split("/")
            normalized = unicodedata.normalize("NFC", relative).casefold()
            if normalized in portable or relative in seen:
                raise InputValidationError("Bundle contains case-insensitive or Unicode-normalized path collisions.")
            portable.add(normalized)
            seen.add(relative)
            prefix: list[str] = []
            for index, part in enumerate(parts):
                prefix.append(part)
                spelling = "/".join(prefix)
                key = unicodedata.normalize("NFC", spelling).casefold()
                is_file = index == len(parts) - 1
                previous = nodes.get(key)
                if previous:
                    old_spelling, old_is_file = previous
                    if old_spelling != spelling:
                        raise InputValidationError("Bundle contains colliding path components.")
                    if old_is_file or is_file:
                        raise InputValidationError("Bundle contains a file/directory path collision.")
                else:
                    nodes[key] = (spelling, is_file)
            digest = hashlib.sha256()
            size = 0
            try:
                with path.open("rb") as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                        size += len(chunk)
                        digest.update(chunk)
            except OSError as exc:
                raise InputValidationError(f"Unable to read bundle file: {relative}") from exc
            entries.append({"path": relative, "size": size, "sha256": digest.hexdigest()})
    entries.sort(key=lambda item: item["path"])
    if not entries:
        raise InputValidationError("Portable bundle must contain at least one file.")
    return entries


def create_unsigned_manifest(
    bundle: Path, *, platform: str, runtime_version: str
) -> dict:
    if not isinstance(platform, str) or not platform.strip() or len(platform) > 100:
        raise InputValidationError("Platform must be non-empty text of at most 100 characters.")
    if not isinstance(runtime_version, str) or not runtime_version.strip() or len(runtime_version) > 100:
        raise InputValidationError("Runtime version must be non-empty text of at most 100 characters.")
    root = Path(bundle).expanduser()
    files = inventory_bundle(root)
    return {
        "schema_version": 1,
        "product": "Yuki Portable Runtime",
        "artifact_kind": "portable-runtime",
        "platform": platform,
        "runtime_version": runtime_version,
        "files": files,
    }


def write_unsigned_manifest(bundle: Path, *, platform: str, runtime_version: str) -> dict:
    root = Path(bundle).expanduser()
    manifest_path = root / MANIFEST_NAME
    signature_path = root / SIGNATURE_NAME
    if manifest_path.exists() or manifest_path.is_symlink() or signature_path.exists() or signature_path.is_symlink():
        raise InputValidationError("Manifest or signature already exists; refusing to overwrite release metadata.")
    value = create_unsigned_manifest(root, platform=platform, runtime_version=runtime_version)
    payload = (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    fd, temporary = tempfile.mkstemp(prefix=".yuki-manifest-", dir=root)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, manifest_path)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
    return {"manifest": str(manifest_path), "file_count": len(value["files"]), "signed": False, "schema_version": 1}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", required=True, type=Path)
    parser.add_argument("--platform", required=True)
    parser.add_argument("--runtime-version", required=True)
    args = parser.parse_args(argv)
    try:
        report = write_unsigned_manifest(args.bundle, platform=args.platform, runtime_version=args.runtime_version)
    except (OSError, ValueError, InputValidationError) as exc:
        print(f"Portable manifest generation failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
