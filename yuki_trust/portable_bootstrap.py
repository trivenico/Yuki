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
    required = {"schema_version", "product", "artifact_kind", "platform", "runtime_version", "release_sequence", "files"}
    if not isinstance(value, dict) or set(value) != required:
        raise BootstrapError("Runtime manifest has an invalid schema.")
    if (
        value["schema_version"] != 2
        or value["product"] != "Yuki Portable Runtime"
        or value["artifact_kind"] != "portable-runtime"
        or not isinstance(value["platform"], str) or not value["platform"] or len(value["platform"]) > 100
        or not isinstance(value["runtime_version"], str) or not value["runtime_version"] or len(value["runtime_version"]) > 100
        or type(value["release_sequence"]) is not int or value["release_sequence"] < 0
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


def _inventory_posix(root: Path) -> list[dict[str, Any]]:
    """Inventory through pinned directory descriptors and no-follow opens."""
    if (
        not hasattr(os, "O_DIRECTORY")
        or not hasattr(os, "O_NOFOLLOW")
        or os.open not in os.supports_dir_fd
        or os.stat not in os.supports_dir_fd
    ):
        raise BootstrapError("POSIX bundle inventory requires dir_fd, O_DIRECTORY and O_NOFOLLOW support.")
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    file_flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NONBLOCK", 0)
    files: list[dict[str, Any]] = []

    def visit(directory_fd: int, prefix: str) -> None:
        try:
            names = sorted(os.listdir(directory_fd))
        except OSError as exc:
            raise BootstrapError("Bundle directory changed while being inspected.") from exc
        for name in names:
            relative = f"{prefix}/{name}" if prefix else name
            if not prefix and relative in {MANIFEST_NAME, SIGNATURE_NAME}:
                continue
            try:
                entry = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
                if stat.S_ISDIR(entry.st_mode):
                    child_fd = os.open(name, directory_flags, dir_fd=directory_fd)
                    try:
                        opened = os.fstat(child_fd)
                        if not stat.S_ISDIR(opened.st_mode) or (opened.st_dev, opened.st_ino) != (entry.st_dev, entry.st_ino):
                            raise BootstrapError(f"Bundle directory changed during inventory: {relative}")
                        visit(child_fd, relative)
                    finally:
                        os.close(child_fd)
                    continue
                if not stat.S_ISREG(entry.st_mode):
                    raise BootstrapError(f"Bundle contains a symbolic-link or non-regular file: {relative}")
                file_fd = os.open(name, file_flags, dir_fd=directory_fd)
                try:
                    before = os.fstat(file_fd)
                    if not stat.S_ISREG(before.st_mode) or (before.st_dev, before.st_ino) != (entry.st_dev, entry.st_ino):
                        raise BootstrapError(f"Bundle file changed during inventory: {relative}")
                    digest = hashlib.sha256()
                    size = 0
                    while True:
                        chunk = os.read(file_fd, 1024 * 1024)
                        if not chunk:
                            break
                        size += len(chunk)
                        digest.update(chunk)
                    after = os.fstat(file_fd)
                    if (
                        size != before.st_size
                        or (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
                        != (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                    ):
                        raise BootstrapError(f"Bundle file changed while being hashed: {relative}")
                    files.append({"path": relative, "size": size, "sha256": digest.hexdigest()})
                finally:
                    os.close(file_fd)
            except BootstrapError:
                raise
            except OSError as exc:
                raise BootstrapError(f"Unable to securely inspect bundle entry: {relative}") from exc

    root_fd = None
    try:
        root_fd = os.open(root, directory_flags)
        root_info = os.fstat(root_fd)
        if not stat.S_ISDIR(root_info.st_mode):
            raise BootstrapError("Portable bundle root must be a real directory.")
        visit(root_fd, "")
    except BootstrapError:
        raise
    except OSError as exc:
        raise BootstrapError("Unable to securely open portable bundle root.") from exc
    finally:
        if root_fd is not None:
            os.close(root_fd)
    files.sort(key=lambda item: item["path"])
    if not files:
        raise BootstrapError("Runtime bundle contains no regular files.")
    return files


def _inventory(root: Path) -> list[dict[str, Any]]:
    if os.name != "nt":
        return _inventory_posix(root)

    # Windows currently uses the path-based fallback. A handle-based
    # FILE_FLAG_OPEN_REPARSE_POINT implementation is a separate hardening task.
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


def _read_bundle_metadata_posix(root: Path, name: str, limit: int) -> bytes:
    """Read a root-level metadata file through pinned descriptors and O_NOFOLLOW."""
    if (
        not hasattr(os, "O_DIRECTORY")
        or not hasattr(os, "O_NOFOLLOW")
        or os.open not in os.supports_dir_fd
        or os.stat not in os.supports_dir_fd
    ):
        raise BootstrapError("POSIX metadata reads require descriptor-relative no-follow support.")
    flags = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0))
    try:
        entry = os.stat(name, dir_fd=root_fd, follow_symlinks=False)
        if not stat.S_ISREG(entry.st_mode) or entry.st_size > limit:
            raise BootstrapError(f"Runtime metadata file is missing, oversized, or not regular: {name}")
        fd = os.open(name, flags | getattr(os, "O_NONBLOCK", 0), dir_fd=root_fd)
        try:
            before = os.fstat(fd)
            if not stat.S_ISREG(before.st_mode) or (before.st_dev, before.st_ino) != (entry.st_dev, entry.st_ino):
                raise BootstrapError(f"Runtime metadata changed while being opened: {name}")
            chunks: list[bytes] = []
            total = 0
            while True:
                chunk = os.read(fd, min(65536, limit + 1 - total))
                if not chunk:
                    break
                total += len(chunk)
                if total > limit:
                    raise BootstrapError(f"Runtime metadata file is oversized: {name}")
                chunks.append(chunk)
            after = os.fstat(fd)
            if (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns) != (
                before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns
            ):
                raise BootstrapError(f"Runtime metadata changed while being read: {name}")
            return b"".join(chunks)
        finally:
            os.close(fd)
    except OSError as exc:
        raise BootstrapError(f"Runtime metadata is missing or unable to be securely read: {name}") from exc
    finally:
        os.close(root_fd)


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
    if os.name != "nt":
        manifest_bytes = _read_bundle_metadata_posix(root, MANIFEST_NAME, MAX_MANIFEST_BYTES)
        signature_bytes = _read_bundle_metadata_posix(root, SIGNATURE_NAME, MAX_SIGNATURE_BYTES)
    else:
        from yuki_trust.portable_host import _read_host_file_secure

        manifest_bytes = _read_host_file_secure(
            root / MANIFEST_NAME, label="Runtime manifest", max_bytes=MAX_MANIFEST_BYTES
        )
        signature_bytes = _read_host_file_secure(
            root / SIGNATURE_NAME, label="Runtime signature", max_bytes=MAX_SIGNATURE_BYTES
        )

    try:
        value = json.loads(manifest_bytes.decode("utf-8"), object_pairs_hook=_reject_duplicate_json_keys)
    except (UnicodeError, json.JSONDecodeError) as exc:
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

        # Import lazily to avoid the portable_host -> portable_bootstrap
        # module dependency becoming an import cycle. The same host-side
        # descriptor-relative reader protects both policy and trust-anchor reads.
        from yuki_trust.portable_host import _read_host_file_secure

        public_key_bytes = _read_host_file_secure(
            key_path, label="Host trust anchor", max_bytes=65536
        )
        public_key = serialization.load_pem_public_key(public_key_bytes)
        signature = base64.b64decode(signature_bytes.decode("ascii").strip(), validate=True)
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


def _copy_bundle_posix(source: Path, staged: Path, manifest: dict[str, Any]) -> None:
    """Copy and re-hash bundle content using pinned source and destination directory FDs."""
    if (
        not hasattr(os, "O_DIRECTORY")
        or not hasattr(os, "O_NOFOLLOW")
        or os.open not in os.supports_dir_fd
        or os.stat not in os.supports_dir_fd
        or os.mkdir not in os.supports_dir_fd
    ):
        raise BootstrapError("POSIX staging requires descriptor-relative filesystem primitives.")
    nofollow = os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | nofollow
    source_fd = os.open(source, directory_flags)
    target_fd = os.open(staged, directory_flags)
    try:
        def open_directory(parent_fd: int, name: str, *, create: bool) -> int:
            if create:
                try:
                    os.mkdir(name, 0o700, dir_fd=parent_fd)
                except FileExistsError:
                    pass
            entry = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
            if not stat.S_ISDIR(entry.st_mode):
                raise BootstrapError("Bundle staging encountered a non-directory path component.")
            child_fd = os.open(name, directory_flags, dir_fd=parent_fd)
            opened = os.fstat(child_fd)
            if (opened.st_dev, opened.st_ino) != (entry.st_dev, entry.st_ino):
                os.close(child_fd)
                raise BootstrapError("Directory changed while staging the portable bundle.")
            return child_fd

        def parent_fd_for(root_fd: int, parts: tuple[str, ...], *, create: bool) -> int:
            current = os.dup(root_fd)
            try:
                for part in parts:
                    child = open_directory(current, part, create=create)
                    os.close(current)
                    current = child
                return current
            except Exception:
                os.close(current)
                raise

        def copy_metadata_file(name: str, limit: int) -> None:
            entry = os.stat(name, dir_fd=source_fd, follow_symlinks=False)
            if not stat.S_ISREG(entry.st_mode) or entry.st_size > limit:
                raise BootstrapError(f"Bundle metadata file is invalid: {name}")
            fd = os.open(name, os.O_RDONLY | nofollow | getattr(os, "O_NONBLOCK", 0), dir_fd=source_fd)
            try:
                before = os.fstat(fd)
                if (before.st_dev, before.st_ino) != (entry.st_dev, entry.st_ino):
                    raise BootstrapError(f"Bundle metadata changed during staging: {name}")
                chunks: list[bytes] = []
                total = 0
                while True:
                    chunk = os.read(fd, min(65536, limit + 1 - total))
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > limit:
                        raise BootstrapError(f"Bundle metadata file is oversized: {name}")
                    chunks.append(chunk)
                after = os.fstat(fd)
                if (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns) != (
                    before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns
                ):
                    raise BootstrapError(f"Bundle metadata changed while staging: {name}")
                data = b"".join(chunks)
            finally:
                os.close(fd)
            out_fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | nofollow, 0o600, dir_fd=target_fd)
            try:
                view = memoryview(data)
                while view:
                    written = os.write(out_fd, view)
                    if written <= 0:
                        raise BootstrapError("Unable to write staged bundle metadata.")
                    view = view[written:]
            finally:
                os.close(out_fd)

        for item in manifest["files"]:
            relative = PurePosixPath(item["path"])
            source_parent = parent_fd_for(source_fd, relative.parts[:-1], create=False)
            target_parent = parent_fd_for(target_fd, relative.parts[:-1], create=True)
            source_file_fd = target_file_fd = None
            try:
                name = relative.parts[-1]
                entry = os.stat(name, dir_fd=source_parent, follow_symlinks=False)
                if not stat.S_ISREG(entry.st_mode):
                    raise BootstrapError(f"Bundle changed during staging: {item['path']}")
                source_file_fd = os.open(name, os.O_RDONLY | nofollow | getattr(os, "O_NONBLOCK", 0), dir_fd=source_parent)
                before = os.fstat(source_file_fd)
                if not stat.S_ISREG(before.st_mode) or (before.st_dev, before.st_ino) != (entry.st_dev, entry.st_ino):
                    raise BootstrapError(f"Bundle file changed during staging: {item['path']}")
                target_file_fd = os.open(
                    name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | nofollow,
                    stat.S_IMODE(before.st_mode) & 0o755, dir_fd=target_parent
                )
                digest = hashlib.sha256()
                size = 0
                while True:
                    chunk = os.read(source_file_fd, 1024 * 1024)
                    if not chunk:
                        break
                    size += len(chunk)
                    digest.update(chunk)
                    view = memoryview(chunk)
                    while view:
                        written = os.write(target_file_fd, view)
                        if written <= 0:
                            raise BootstrapError("Unable to write staged runtime file.")
                        view = view[written:]
                after = os.fstat(source_file_fd)
                if (
                    size != item["size"]
                    or digest.hexdigest() != item["sha256"]
                    or (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
                    != (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns)
                ):
                    raise BootstrapError(f"Bundle content changed during staging: {item['path']}")
                os.fchmod(target_file_fd, stat.S_IMODE(before.st_mode) & 0o755)
            except OSError as exc:
                raise BootstrapError(f"Unable to securely stage bundle file: {item['path']}") from exc
            finally:
                if source_file_fd is not None:
                    os.close(source_file_fd)
                if target_file_fd is not None:
                    os.close(target_file_fd)
                os.close(source_parent)
                os.close(target_parent)

        copy_metadata_file(MANIFEST_NAME, MAX_MANIFEST_BYTES)
        copy_metadata_file(SIGNATURE_NAME, MAX_SIGNATURE_BYTES)
    finally:
        os.close(source_fd)
        os.close(target_fd)


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
        if os.name != "nt":
            _copy_bundle_posix(source, staged, manifest)
        else:
            # Windows still needs a native handle-based no-reparse copy path.
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
    anti_rollback_state_path: str | Path,
) -> int:
    def validate_relative(value: str, label: str) -> PurePosixPath:
        if not isinstance(value, str) or not value or "\\" in value:
            raise BootstrapError(f"{label} must be a canonical relative path inside the signed bundle.")
        relative = PurePosixPath(value)
        if relative.is_absolute() or any(part in {"", ".", ".."} for part in value.split("/")) or relative.as_posix() != value:
            raise BootstrapError(f"{label} must be a canonical relative path inside the signed bundle.")
        return relative

    python_relative = validate_relative(python_executable, "Python interpreter")
    entry_relative = validate_relative(entrypoint, "Entrypoint")
    state_path = Path(anti_rollback_state_path).expanduser()
    if not state_path.is_absolute() or not state_path.parent.is_dir():
        raise BootstrapError("Anti-rollback state path must be absolute and use an existing host directory.")
    current_parent = state_path.parent
    while True:
        info = current_parent.lstat()
        if stat.S_ISLNK(info.st_mode):
            if current_parent != Path("/var") or current_parent.resolve(strict=True) != Path("/private/var"):
                raise BootstrapError("Anti-rollback state parent directories must not contain symlinks.")
        elif not stat.S_ISDIR(info.st_mode):
            raise BootstrapError("Anti-rollback state parent directories must be real directories.")
        if os.name != "nt":
            if info.st_uid not in {0, os.getuid()}:
                raise BootstrapError("Anti-rollback state parent directory has an untrusted owner.")
            mode = stat.S_IMODE(info.st_mode)
            if mode & 0o022 and not (mode & stat.S_ISVTX):
                raise BootstrapError("Anti-rollback state parent directory is group- or world-writable.")
        if current_parent.parent == current_parent:
            break
        current_parent = current_parent.parent
    try:
        canonical_state_parent = state_path.parent.resolve(strict=True)
    except OSError as exc:
        raise BootstrapError("Anti-rollback state parent directory is unavailable.") from exc
    source_root = Path(bundle).expanduser().resolve(strict=True)
    resolved_state = canonical_state_parent / state_path.name
    try:
        resolved_state.relative_to(source_root)
    except ValueError:
        pass
    else:
        raise BootstrapError("Anti-rollback state must be stored outside the removable bundle.")
    if resolved_state.is_symlink():
        raise BootstrapError("Anti-rollback state file must not be a symlink.")
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
        # Import lazily: anti_rollback uses BootstrapError from this module.
        from yuki_trust.anti_rollback import advance_release_state
        manifest_bytes = (staged / MANIFEST_NAME).read_bytes()
        advance_release_state(
            resolved_state,
            manifest["release_sequence"],
            manifest_bytes,
        )
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
    parser.add_argument("--anti-rollback-state", required=True, type=Path, help="Persistent host state file outside removable media")
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
            args.entrypoint,
            forwarded,
            expected_platform=args.expected_platform,
            anti_rollback_state_path=args.anti_rollback_state,
        )
    except (OSError, ValueError, BootstrapError) as exc:
        print(f"Yuki portable bootstrap refused to launch: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
