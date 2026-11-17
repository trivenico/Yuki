#!/usr/bin/env python3
"""Validate provenance and integrity of local wake-word training assets.

The manifest is deliberately explicit: a license label alone is not proof of
permission. A human must review the source terms and set rights_reviewed=true.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import sys
from typing import Any


class ManifestError(ValueError):
    """The training asset manifest is incomplete or unsafe."""


REQUIRED_ASSET_FIELDS = {
    "path",
    "role",
    "sha256",
    "license",
    "license_url",
    "source_url",
    "rights_reviewed",
    "allow_training",
    "allow_redistribution",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_asset_path(root: Path, raw_path: Any) -> Path:
    if not isinstance(raw_path, str) or not raw_path or "\x00" in raw_path:
        raise ManifestError("Asset path must be a non-empty relative string.")
    relative = PurePosixPath(raw_path)
    if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
        raise ManifestError(f"Unsafe asset path: {raw_path!r}")
    if "\\" in raw_path:
        raise ManifestError(f"Asset paths must use forward slashes: {raw_path!r}")

    candidate = root
    for part in relative.parts:
        candidate = candidate / part
        if candidate.is_symlink():
            raise ManifestError(f"Symlinked training assets are not allowed: {raw_path!r}")
    try:
        resolved = candidate.resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, ValueError) as exc:
        raise ManifestError(f"Asset is missing or escapes the dataset root: {raw_path!r}") from exc
    if not resolved.is_file():
        raise ManifestError(f"Training asset is not a regular file: {raw_path!r}")
    return resolved


def validate_manifest(
    manifest_path: Path,
    *,
    require_redistribution: bool = False,
    verify_hashes: bool = True,
) -> list[str]:
    manifest_path = manifest_path.resolve(strict=True)
    root = manifest_path.parent.resolve(strict=True)
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ManifestError("Manifest must be valid UTF-8 JSON.") from exc

    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise ManifestError("Manifest schema_version must be 1.")
    assets = data.get("assets")
    if not isinstance(assets, list) or not assets or len(assets) > 100_000:
        raise ManifestError("Manifest must contain between 1 and 100000 assets.")

    seen: set[str] = set()
    validated: list[str] = []
    for index, asset in enumerate(assets):
        if not isinstance(asset, dict) or not REQUIRED_ASSET_FIELDS.issubset(asset):
            raise ManifestError(f"Asset entry {index} is missing required provenance fields.")
        path_value = asset["path"]
        if path_value in seen:
            raise ManifestError(f"Duplicate asset path: {path_value!r}")
        seen.add(path_value)
        if asset["rights_reviewed"] is not True or asset["allow_training"] is not True:
            raise ManifestError(f"Asset {path_value!r} has not been approved for training.")
        if require_redistribution and asset["allow_redistribution"] is not True:
            raise ManifestError(f"Asset {path_value!r} is not approved for redistribution.")
        for field in ("role", "license", "license_url", "source_url"):
            value = asset[field]
            if not isinstance(value, str) or not value.strip():
                raise ManifestError(f"Asset {path_value!r} has an empty {field}.")
        checksum = asset["sha256"]
        if not isinstance(checksum, str) or len(checksum) != 64 or any(c not in "0123456789abcdef" for c in checksum):
            raise ManifestError(f"Asset {path_value!r} has an invalid SHA-256 digest.")

        resolved = _safe_asset_path(root, path_value)
        if verify_hashes and _sha256(resolved) != checksum:
            raise ManifestError(f"SHA-256 mismatch for asset {path_value!r}.")
        validated.append(path_value)
    return validated


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--require-redistribution", action="store_true")
    parser.add_argument("--skip-hashes", action="store_true")
    args = parser.parse_args(argv)
    try:
        assets = validate_manifest(
            args.manifest,
            require_redistribution=args.require_redistribution,
            verify_hashes=not args.skip_hashes,
        )
    except (ManifestError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(f"Validated {len(assets)} training assets.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
