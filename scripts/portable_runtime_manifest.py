"""Build and verify an unsigned integrity manifest for portable runtime bundles.

A SHA-256 manifest detects accidental or post-build file changes. It is not a
signature and does not authenticate who produced the bundle.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import stat
import sys
import unicodedata
from pathlib import Path, PurePosixPath
from typing import Any

MANIFEST_NAME = "runtime-manifest.json"
SIGNATURE_NAME = "runtime-manifest.sig"
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
            if relative in {MANIFEST_NAME, SIGNATURE_NAME}:
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
    manifest = {
        "schema_version": 1,
        "product": "Yuki Portable Runtime",
        "artifact_kind": "unsigned-portable-runtime",
        "platform": platform.strip(),
        "runtime_version": runtime_version.strip(),
        "files": _inventory(bundle),
    }
    _validate_manifest(manifest)
    return manifest


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
    portable_names: set[str] = set()
    for item in value["files"]:
        if not isinstance(item, dict) or set(item) != {"path", "size", "sha256"}:
            raise RuntimeManifestError("Runtime manifest file entry is invalid.")
        relative = item["path"]
        if not isinstance(relative, str) or not relative or "\\" in relative:
            raise RuntimeManifestError("Runtime manifest path is invalid.")
        parsed = PurePosixPath(relative)
        segments = relative.split("/")
        if parsed.is_absolute() or any(part in {"", ".", ".."} for part in segments):
            raise RuntimeManifestError("Runtime manifest path escapes the bundle.")
        for segment in segments:
            if (
                segment.endswith((" ", "."))
                or any(ord(char) < 32 or char in '<>:"|?*' for char in segment)
                or segment.split(".", 1)[0].rstrip(" .").upper() in {
                    "CON", "PRN", "AUX", "NUL",
                    *(f"COM{number}" for number in range(1, 10)),
                    *(f"LPT{number}" for number in range(1, 10)),
                }
            ):
                raise RuntimeManifestError("Runtime manifest path is not portable to Windows.")
        portable_name = unicodedata.normalize("NFC", relative).casefold()
        if portable_name in portable_names:
            raise RuntimeManifestError("Runtime manifest contains duplicate, case-insensitive, or Unicode-normalized path collisions.")
        portable_names.add(portable_name)
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



def _external_regular_file(path: str | Path, bundle: Path, label: str) -> Path:
    candidate = Path(path).expanduser()
    if candidate.is_symlink() or not candidate.is_file() or candidate.stat().st_size > 65536:
        raise RuntimeManifestError(f"{label} must be an existing regular non-symlink file no larger than 64 KiB.")
    resolved = candidate.resolve(strict=True)
    try:
        resolved.relative_to(bundle)
    except ValueError:
        return resolved
    raise RuntimeManifestError(f"{label} must be stored outside the portable bundle.")


def sign_manifest(bundle: str | Path, private_key_path: str | Path) -> Path:
    root = Path(bundle).expanduser().resolve(strict=True)
    verify_manifest(root)
    key_path = _external_regular_file(private_key_path, root, "Signing private key")
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    except ImportError as exc:
        raise RuntimeManifestError("Signing requires the optional cryptography dependency.") from exc
    try:
        key = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
    except (OSError, ValueError, TypeError) as exc:
        raise RuntimeManifestError("Signing private key is unreadable or invalid PEM.") from exc
    if not isinstance(key, Ed25519PrivateKey):
        raise RuntimeManifestError("Only Ed25519 release signing keys are accepted.")
    signature_path = root / SIGNATURE_NAME
    if signature_path.is_symlink() or (signature_path.exists() and not signature_path.is_file()):
        raise RuntimeManifestError("Signature destination must be a regular non-symlink file.")
    manifest_bytes = (root / MANIFEST_NAME).read_bytes()
    signature = base64.b64encode(key.sign(manifest_bytes)).decode("ascii") + "\n"
    signature_path.write_text(signature, encoding="ascii")
    return signature_path


def verify_signed_manifest(
    bundle: str | Path,
    public_key_path: str | Path,
) -> dict[str, Any]:
    root = Path(bundle).expanduser().resolve(strict=True)
    manifest = verify_manifest(root)
    key_path = _external_regular_file(public_key_path, root, "Trusted release public key")
    signature_path = root / SIGNATURE_NAME
    if signature_path.is_symlink() or not signature_path.is_file() or signature_path.stat().st_size > 1024:
        raise RuntimeManifestError("Detached runtime signature is missing or invalid.")
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        signature = base64.b64decode(signature_path.read_text(encoding="ascii").strip(), validate=True)
        key = serialization.load_pem_public_key(key_path.read_bytes())
    except ImportError as exc:
        raise RuntimeManifestError("Signature verification requires the optional cryptography dependency.") from exc
    except (OSError, UnicodeError, ValueError, TypeError) as exc:
        raise RuntimeManifestError("Trusted key or detached signature is unreadable or invalid.") from exc
    if not isinstance(key, Ed25519PublicKey):
        raise RuntimeManifestError("Only Ed25519 trusted release public keys are accepted.")
    try:
        key.verify(signature, (root / MANIFEST_NAME).read_bytes())
    except Exception as exc:
        raise RuntimeManifestError("Runtime release signature verification failed.") from exc
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("create")
    build.add_argument("bundle", type=Path)
    build.add_argument("--platform", required=True)
    build.add_argument("--runtime-version", required=True)
    sign = sub.add_parser("sign")
    sign.add_argument("bundle", type=Path)
    sign.add_argument("--private-key", required=True, type=Path)
    verify = sub.add_parser("verify")
    verify.add_argument("bundle", type=Path)
    signed = sub.add_parser("verify-signed")
    signed.add_argument("bundle", type=Path)
    signed.add_argument("--public-key", required=True, type=Path)
    args = parser.parse_args()
    try:
        if args.command == "create":
            bundle = args.bundle.expanduser().resolve(strict=True)
            manifest = create_manifest(bundle, platform=args.platform, runtime_version=args.runtime_version)
            output = bundle / MANIFEST_NAME
            if output.is_symlink() or (output.exists() and not output.is_file()):
                raise RuntimeManifestError("Runtime manifest destination must be a regular non-symlink file.")
            output.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            print(f"Created unsigned integrity manifest for {len(manifest['files'])} files: {output}")
        elif args.command == "sign":
            signature = sign_manifest(args.bundle, args.private_key)
            print(f"Created detached Ed25519 release signature: {signature}")
        elif args.command == "verify-signed":
            manifest = verify_signed_manifest(args.bundle, args.public_key)
            print(f"Verified signed release: {len(manifest['files'])} files for {manifest['platform']}.")
        else:
            manifest = verify_manifest(args.bundle)
            print(f"Verified {len(manifest['files'])} runtime files for {manifest['platform']}.")
    except (OSError, ValueError, RuntimeManifestError) as exc:
        print(f"Portable runtime manifest check failed: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
