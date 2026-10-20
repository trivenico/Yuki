"""Authenticated, bounded, no-plaintext-temp backup archive primitives.

Install the optional `backup` extra to enable AES-256-GCM. Keys are supplied by
the caller (for example, from an OS key store); this module never writes keys.
"""
from __future__ import annotations

import io
import os
import secrets
import tarfile
import tempfile
from pathlib import Path, PurePosixPath

MAGIC = b"YUKIBAK1"
NONCE_BYTES = 12
KEY_BYTES = 32
DEFAULT_MAX_BYTES = 64 * 1024 * 1024


class BackupError(ValueError):
    """Raised when a backup cannot be safely created or restored."""


def _aesgcm(key: bytes):
    if not isinstance(key, bytes) or len(key) != KEY_BYTES:
        raise BackupError("Backup key must be exactly 32 bytes.")
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError as exc:
        raise RuntimeError(
            "Encrypted backups require the optional dependency: pip install '.[backup]'."
        ) from exc
    return AESGCM(key)


def _safe_relative(path: str) -> PurePosixPath:
    candidate = PurePosixPath(path)
    if candidate.is_absolute() or not candidate.parts or any(
        part in {"", ".", ".."} for part in candidate.parts
    ):
        raise BackupError("Archive contains an unsafe path.")
    if "\\" in path or "\x00" in path:
        raise BackupError("Archive contains an invalid path.")
    return candidate


def create_encrypted_backup(sources, output_path, key: bytes, *, max_bytes=DEFAULT_MAX_BYTES):
    """Archive regular files/directories and encrypt before writing any output.

    Symlinks and special files are rejected. Archive content is bounded and is
    never written as plaintext to disk. The caller owns key storage/rotation.
    """
    aes = _aesgcm(key)
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes < 1:
        raise BackupError("max_bytes must be a positive integer.")
    raw_sources = [Path(item).expanduser() for item in sources]
    if not raw_sources:
        raise BackupError("At least one backup source is required.")
    if any(path.is_symlink() for path in raw_sources):
        raise BackupError("Symlinks are not allowed as backup sources.")
    source_paths = [path.resolve(strict=True) for path in raw_sources]

    payload = io.BytesIO()
    seen = set()
    total = 0
    with tarfile.open(fileobj=payload, mode="w", format=tarfile.PAX_FORMAT) as archive:
        for source in source_paths:
            candidates = [source]
            if source.is_dir():
                candidates.extend(sorted(source.rglob("*")))
            for candidate in candidates:
                if candidate.is_symlink():
                    raise BackupError("Symlinks are not allowed in backups.")
                if not (candidate.is_file() or candidate.is_dir()):
                    raise BackupError("Only regular files and directories can be backed up.")
                # Store a source basename as the top-level archive path.
                rel = PurePosixPath(source.name)
                if candidate != source:
                    rel = rel.joinpath(*candidate.relative_to(source).parts)
                name = rel.as_posix()
                if name in seen:
                    raise BackupError("Backup sources contain colliding archive paths.")
                seen.add(name)
                info = archive.gettarinfo(str(candidate), arcname=name)
                if info.issym() or info.islnk() or not (info.isfile() or info.isdir()):
                    raise BackupError("Special files are not allowed in backups.")
                if info.isfile():
                    total += info.size
                    if total > max_bytes:
                        raise BackupError("Backup exceeds the configured size limit.")
                    with candidate.open("rb") as handle:
                        archive.addfile(info, handle)
                else:
                    archive.addfile(info)
                if payload.tell() > max_bytes + 1024 * 1024:
                    raise BackupError("Archive exceeds the configured size limit.")

    plaintext = payload.getvalue()
    if len(plaintext) > max_bytes + 1024 * 1024:
        raise BackupError("Archive exceeds the configured size limit.")
    nonce = secrets.token_bytes(NONCE_BYTES)
    encrypted = MAGIC + nonce + aes.encrypt(nonce, plaintext, MAGIC)
    target = Path(output_path).expanduser().absolute()
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=".yuki-backup-", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(encrypted)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, target)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise
    return target


def restore_encrypted_backup(backup_path, destination, key: bytes, *, max_bytes=DEFAULT_MAX_BYTES):
    """Decrypt and safely restore into an empty destination directory."""
    aes = _aesgcm(key)
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes < 1:
        raise BackupError("max_bytes must be a positive integer.")
    source = Path(backup_path).expanduser()
    header_len = len(MAGIC) + NONCE_BYTES
    try:
        encrypted_size = source.stat().st_size
    except OSError as exc:
        raise BackupError("Backup file cannot be read.") from exc
    if encrypted_size > max_bytes + 2 * 1024 * 1024:
        raise BackupError("Encrypted backup exceeds the configured size limit.")
    encrypted = source.read_bytes()
    if len(encrypted) < header_len + 16 or not encrypted.startswith(MAGIC):
        raise BackupError("Backup format is invalid.")
    if len(encrypted) > max_bytes + 2 * 1024 * 1024:
        raise BackupError("Encrypted backup exceeds the configured size limit.")
    nonce = encrypted[len(MAGIC):header_len]
    try:
        plaintext = aes.decrypt(nonce, encrypted[header_len:], MAGIC)
    except Exception as exc:
        raise BackupError("Backup authentication failed; no files were restored.") from exc
    if len(plaintext) > max_bytes + 1024 * 1024:
        raise BackupError("Decrypted archive exceeds the configured size limit.")

    target = Path(destination).expanduser().absolute()
    if target.is_symlink():
        raise BackupError("Restore destination cannot be a symlink.")
    target.mkdir(parents=True, exist_ok=True)
    if any(target.iterdir()):
        raise BackupError("Restore destination must be empty.")
    created = []
    try:
        with tarfile.open(fileobj=io.BytesIO(plaintext), mode="r:") as archive:
            members = archive.getmembers()
            total = 0
            validated = []
            names = set()
            file_names = set()
            for member in members:
                rel = _safe_relative(member.name)
                if not (member.isdir() or member.isfile()) or member.issym() or member.islnk():
                    raise BackupError("Archive contains unsupported file types.")
                total += member.size if member.isfile() else 0
                if total > max_bytes:
                    raise BackupError("Restored data exceeds the configured size limit.")
                name = rel.as_posix()
                if name in names:
                    raise BackupError("Archive contains duplicate paths.")
                if any(parent in file_names for parent in rel.parents if str(parent) != "."):
                    raise BackupError("Archive file is used as a parent directory.")
                names.add(name)
                if member.isfile():
                    if any(name.startswith(existing + "/") for existing in file_names):
                        raise BackupError("Archive contains colliding paths.")
                    file_names.add(name)
                out = target.joinpath(*rel.parts)
                if out != target and target not in out.parents:
                    raise BackupError("Archive path escapes restore destination.")
                validated.append((member, out))
            for member, out in validated:
                if member.isdir():
                    out.mkdir(parents=True, exist_ok=True)
                    created.append(out)
                    continue
                out.parent.mkdir(parents=True, exist_ok=True)
                if out.exists():
                    raise BackupError("Archive contains colliding paths.")
                stream = archive.extractfile(member)
                if stream is None:
                    raise BackupError("Archive file content is missing.")
                with stream, out.open("xb") as handle:
                    while True:
                        chunk = stream.read(1024 * 1024)
                        if not chunk:
                            break
                        handle.write(chunk)
                created.append(out)
    except Exception:
        # Best-effort cleanup of this restore's outputs. Destination is required empty.
        for item in sorted(created, key=lambda p: len(p.parts), reverse=True):
            try:
                if item.is_file() or item.is_symlink():
                    item.unlink()
                elif item.is_dir():
                    item.rmdir()
            except OSError:
                pass
        raise
    return target
