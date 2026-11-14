"""Host-installed trust bootstrap for Yuki portable runtime bundles.

This module must be installed on the host from an independently trusted
package source. It intentionally does not import code from the removable
bundle before authenticating and staging its complete signed inventory.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unicodedata
from pathlib import Path, PurePosixPath
from typing import Any

MANIFEST_NAME = "runtime-manifest.json"
SIGNATURE_NAME = "runtime-manifest.sig"
MAX_MANIFEST_BYTES = 1024 * 1024
MAX_SIGNATURE_BYTES = 1024


class BootstrapError(RuntimeError):
    """Raised when a portable bundle cannot be safely authenticated."""


def _reject_duplicate_json_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise BootstrapError("Runtime manifest contains duplicate JSON object keys.")
        result[key] = value
    return result


def _trusted_key_path(path: str | Path, bundle: Path) -> Path:
    candidate = Path(path).expanduser()
    if candidate.is_symlink() or not candidate.is_file() or candidate.stat().st_size > 65536:
        raise BootstrapError("Trusted public key must be a regular, non-symlink host file no larger than 64 KiB.")
    resolved = candidate.resolve(strict=True)
    try:
        resolved.relative_to(bundle)
    except ValueError:
        return resolved
    raise BootstrapError("Trusted public key must be provisioned outside the removable bundle.")


def _validate_manifest(value: Any) -> list[dict[str, Any]]:
    required = {"schema_version", "product", "artifact_kind", "platform", "runtime_version", "files"}
    if not isinstance(value, dict) or set(value) != required:
        raise BootstrapError("Runtime manifest has an invalid schema.")
    if (
        value["schema_version"] != 1
        or value["product"] != "Yuki Portable Runtime"
        or value["artifact_kind"] != "portable-runtime"
        or not isinstance(value["platform"], str) or not value["platform"] or len(value["platform"]) > 100
        or not isinstance(value["runtime_version"], str) or not value["runtime_version"] or len(value["runtime_version"]) > 100
        or not isinstance(value["files"], list) or not value["files"]
    ):
        raise BootstrapError("Runtime manifest metadata is invalid.")

    seen: set[str] = set()
    portable_names: set[str] = set()
    path_nodes: dict[str, tuple[str, bool]] = {}
    reserved = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
    for item in value["files"]:
        if not isinstance(item, dict) or set(item) != {"path", "size", "sha256"}:
            raise BootstrapError("Runtime manifest file entry is invalid.")
        relative = item["path"]
        if not isinstance(relative, str) or not relative or "\\" in relative:
            raise BootstrapError("Runtime manifest path is invalid.")
        parsed = PurePosixPath(relative)
        segments = relative.split("/")
        if parsed.is_absolute() or any(part in {"", ".", ".."} for part in segments) or parsed.as_posix() != relative:
            raise BootstrapError("Runtime manifest path escapes the bundle or is non-canonical.")
        for segment in segments:
            if (
                segment.endswith((" ", "."))
                or any(ord(char) < 32 or char in '<>:"|?*' for char in segment)
                or segment.split(".", 1)[0].rstrip(" .").upper() in reserved
            ):
                raise BootstrapError("Runtime manifest path is not portable to Windows.")
        portable_name = unicodedata.normalize("NFC", relative).casefold()
        if portable_name in portable_names or relative in {MANIFEST_NAME, SIGNATURE_NAME} or relative in seen:
            raise BootstrapError("Runtime manifest contains duplicate or reserved paths.")
        portable_names.add(portable_name)
        seen.add(relative)

        prefix_parts: list[str] = []
        for index, segment in enumerate(segments):
            prefix_parts.append(segment)
            original_prefix = "/".join(prefix_parts)
            normalized_prefix = unicodedata.normalize("NFC", original_prefix).casefold()
            is_file = index == len(segments) - 1
            existing = path_nodes.get(normalized_prefix)
            if existing is not None:
                existing_spelling, existing_is_file = existing
                if existing_spelling != original_prefix:
                    raise BootstrapError("Runtime manifest contains colliding case-insensitive or Unicode-normalized path components.")
                if existing_is_file or is_file:
                    raise BootstrapError("Runtime manifest contains a file/directory path collision.")
            else:
                path_nodes[normalized_prefix] = (original_prefix, is_file)
        if type(item["size"]) is not int or item["size"] < 0:
            raise BootstrapError("Runtime manifest file size is invalid.")
        digest = item["sha256"]
        if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise BootstrapError("Runtime manifest SHA-256 digest is invalid.")
    if [item["path"] for item in value["files"]] != sorted(seen):
        raise BootstrapError("Runtime manifest entries must be sorted.")
    return value["files"]


def _inventory(root: Path) -> list[dict[str, Any]]:
    files: list[dict[str, Any]] = []
    for current, dirs, names in os.walk(root, followlinks=False):
        current_path = Path(current)
        for directory in list(dirs):
            candidate = current_path / directory
            if candidate.is_symlink() or not candidate.is_dir():
                raise BootstrapError(f"Bundle contains a symbolic-link or invalid directory: {candidate.relative_to(root)}")
        for name in names:
            candidate = current_path / name
            relative = candidate.relative_to(root).as_posix()
            if relative in {MANIFEST_NAME, SIGNATURE_NAME}:
                continue
            try:
                info = candidate.lstat()
            except OSError as exc:
                raise BootstrapError("Bundle inventory changed while being inspected.") from exc
            if not stat.S_ISREG(info.st_mode):
                raise BootstrapError(f"Bundle contains a non-regular file: {relative}")
            digest = hashlib.sha256()
            size = 0
            try:
                with candidate.open("rb") as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                        size += len(chunk)
                        digest.update(chunk)
            except OSError as exc:
                raise BootstrapError(f"Unable to read bundle file: {relative}") from exc
            files.append({"path": relative, "size": size, "sha256": digest.hexdigest()})
    files.sort(key=lambda item: item["path"])
    if not files:
        raise BootstrapError("Runtime bundle contains no regular files.")
    return files


def verify_signed_bundle(
    bundle: str | Path,
    public_key_path: str | Path,
    *,
    expected_platform: str | None = None,
) -> dict[str, Any]:
    root_candidate = Path(bundle).expanduser()
    if root_candidate.is_symlink():
        raise BootstrapError("Portable bundle root must not be a symbolic link.")
    root = root_candidate.resolve(strict=True)
    if not root.is_dir():
        raise BootstrapError("Portable bundle must be a directory.")
    manifest_path = root / MANIFEST_NAME
    signature_path = root / SIGNATURE_NAME
    for path, label, limit in (
        (manifest_path, "Runtime manifest", MAX_MANIFEST_BYTES),
        (signature_path, "Runtime signature", MAX_SIGNATURE_BYTES),
    ):
        if path.is_symlink() or not path.is_file() or path.stat().st_size > limit:
            raise BootstrapError(f"{label} is missing, oversized, or not a regular file.")

    try:
        manifest_bytes = manifest_path.read_bytes()
        value = json.loads(manifest_bytes.decode("utf-8"), object_pairs_hook=_reject_duplicate_json_keys)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BootstrapError("Runtime manifest is unreadable or invalid JSON.") from exc
    expected = _validate_manifest(value)
    if expected_platform is not None:
        if not isinstance(expected_platform, str) or not expected_platform.strip():
            raise BootstrapError("Expected runtime platform must be non-empty text.")
        if value["platform"] != expected_platform:
            raise BootstrapError(
                f"Runtime platform mismatch: expected {expected_platform}, found {value['platform']}."
            )
    key_path = _trusted_key_path(public_key_path, root)
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

        public_key = serialization.load_pem_public_key(key_path.read_bytes())
        signature = base64.b64decode(signature_path.read_text(encoding="ascii").strip(), validate=True)
    except ImportError as exc:
        raise BootstrapError("Host bootstrap requires the cryptography package.") from exc
    except (OSError, UnicodeError, ValueError, TypeError) as exc:
        raise BootstrapError("Host trust anchor or detached signature is unreadable or invalid.") from exc
    if not isinstance(public_key, Ed25519PublicKey):
        raise BootstrapError("Only Ed25519 host trust anchors are accepted.")
    try:
        public_key.verify(signature, manifest_bytes)
    except Exception as exc:
        raise BootstrapError("Portable bundle signature verification failed.") from exc

    if _inventory(root) != expected:
        raise BootstrapError("Portable bundle inventory or file hashes do not match the signed manifest.")
    return value


def stage_verified_bundle(
    bundle: str | Path,
    public_key_path: str | Path,
    *,
    expected_platform: str | None = None,
) -> tempfile.TemporaryDirectory:
    """Copy only authenticated files to host temp storage, then verify the copy."""
    source_candidate = Path(bundle).expanduser()
    if source_candidate.is_symlink():
        raise BootstrapError("Portable bundle root must not be a symbolic link.")
    source = source_candidate.resolve(strict=True)
    manifest = verify_signed_bundle(
        source, public_key_path, expected_platform=expected_platform
    )
    staged_temp = tempfile.TemporaryDirectory(prefix="yuki-verified-")
    staged_root = Path(staged_temp.name).resolve(strict=True)
    try:
        staged_root.relative_to(source)
    except ValueError:
        pass
    else:
        staged_temp.cleanup()
        raise BootstrapError("Host staging directory must be outside the removable bundle.")
    staged = Path(staged_temp.name)
    try:
        try:
            staged.chmod(0o700)
        except OSError:
            pass
        for item in manifest["files"]:
            relative = PurePosixPath(item["path"])
            source_file = source.joinpath(*relative.parts)
            target_file = staged.joinpath(*relative.parts)
            target_file.parent.mkdir(parents=True, exist_ok=True)
            if source_file.is_symlink() or not source_file.is_file():
                raise BootstrapError(f"Bundle changed during staging: {item['path']}")
            shutil.copyfile(source_file, target_file)
            try:
                target_file.chmod(stat.S_IMODE(source_file.stat().st_mode))
            except OSError:
                pass
        shutil.copyfile(source / MANIFEST_NAME, staged / MANIFEST_NAME)
        shutil.copyfile(source / SIGNATURE_NAME, staged / SIGNATURE_NAME)
        verify_signed_bundle(staged, public_key_path, expected_platform=expected_platform)
        return staged_temp
    except Exception:
        staged_temp.cleanup()
        raise


def run_verified_bundle(
    bundle: str | Path,
    public_key_path: str | Path,
    python_executable: str,
    entrypoint: str,
    arguments: list[str] | None = None,
    *,
    expected_platform: str | None = None,
) -> int:
    def validate_relative(value: str, label: str) -> PurePosixPath:
        if not isinstance(value, str) or not value or "\\\\" in value:
            raise BootstrapError(f"{label} must be a canonical relative path inside the signed bundle.")
        relative = PurePosixPath(value)
        if relative.is_absolute() or any(part in {"", ".", ".."} for part in value.split("/")) or relative.as_posix() != value:
            raise BootstrapError(f"{label} must be a canonical relative path inside the signed bundle.")
        return relative

    python_relative = validate_relative(python_executable, "Python interpreter")
    entry_relative = validate_relative(entrypoint, "Entrypoint")
    staged_temp = stage_verified_bundle(
        bundle, public_key_path, expected_platform=expected_platform
    )
    try:
        staged = Path(staged_temp.name)
        manifest = json.loads((staged / MANIFEST_NAME).read_text(encoding="utf-8"))
        signed_paths = {item["path"] for item in manifest["files"]}
        for value, relative, label in (
            (python_executable, python_relative, "Python interpreter"),
            (entrypoint, entry_relative, "Entrypoint"),
        ):
            if value not in signed_paths:
                raise BootstrapError(f"{label} is not part of the signed runtime inventory.")
            candidate = staged.joinpath(*relative.parts)
            if candidate.is_symlink() or not candidate.is_file():
                raise BootstrapError(f"{label} is missing or is not a regular signed file.")
        interpreter = staged.joinpath(*python_relative.parts)
        script = staged.joinpath(*entry_relative.parts)
        if os.name != "nt" and not os.access(interpreter, os.X_OK):
            raise BootstrapError("Signed Python interpreter is not executable on this host.")
        completed = subprocess.run(
            [str(interpreter), "-I", str(script), *(arguments or [])],
            cwd=staged,
            check=False,
        )
        return int(completed.returncode)
    finally:
        staged_temp.cleanup()

def main() -> int:
    parser = argparse.ArgumentParser(description="Authenticate and launch a signed Yuki portable runtime.")
    parser.add_argument("--bundle", required=True, type=Path, help="Path to the removable runtime bundle")
    parser.add_argument("--public-key", required=True, type=Path, help="Independently provisioned host trust anchor")
    parser.add_argument("--python", required=True, help="Signed bundled Python interpreter path relative to the bundle")
    parser.add_argument("--entrypoint", required=True, help="Signed Python script path relative to the bundle")
    parser.add_argument(
        "--expected-platform",
        required=True,
        help="Exact platform identifier expected by this host installer (for example linux-x86_64)",
    )
    parser.add_argument("arguments", nargs=argparse.REMAINDER, help="Arguments passed to the signed entrypoint")
    args = parser.parse_args()
    forwarded = args.arguments[1:] if args.arguments[:1] == ["--"] else args.arguments
    try:
        return run_verified_bundle(
            args.bundle,
            args.public_key,
            args.python,
            args.python,
            args.entrypoint,
            forwarded,
            expected_platform=args.expected_platform,
        )
    except (OSError, ValueError, BootstrapError) as exc:
        print(f"Yuki portable bootstrap refused to launch: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
