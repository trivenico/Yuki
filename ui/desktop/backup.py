"""Portable authenticated encrypted backups for Yuki desktop data.

Backups use a user-supplied passphrase (scrypt + AES-256-GCM), so they can be
restored on another device without exporting an OS keyring secret. The source
is never modified. The first implementation is deliberately bounded to 512 MiB
of uncompressed backup data to keep memory use predictable.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import shutil
import sqlite3
import tarfile
import tempfile
from io import BytesIO
from pathlib import Path, PurePosixPath

MAGIC = b"YUKIBK1\0"
SALT_BYTES = 16
NONCE_BYTES = 12
MAX_BACKUP_BYTES = 512 * 1024 * 1024
MAX_FILES = 10_000
SQLITE_SUFFIXES = {".sqlite", ".sqlite3", ".db"}


class BackupError(RuntimeError):
    """Raised when a backup cannot be safely created or restored."""


def _crypto():
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
        from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
    except ImportError as exc:
        raise BackupError(
            "Encrypted backups require cryptography; install requirements-memory-security.txt."
        ) from exc
    return AESGCM, Scrypt


def _derive(passphrase: str, salt: bytes, Scrypt):
    if not isinstance(passphrase, str) or len(passphrase) < 12 or len(passphrase) > 1024:
        raise BackupError("Backup passphrase must contain 12-1024 characters.")
    try:
        return Scrypt(salt=salt, length=32, n=2**15, r=8, p=1).derive(passphrase.encode("utf-8"))
    except Exception as exc:
        raise BackupError("Could not derive the backup encryption key.") from exc


def _inventory(root: Path) -> list[Path]:
    if root.is_symlink():
        raise BackupError("Backup source must not be a symbolic link.")
    root = root.resolve(strict=True)
    if not root.is_dir():
        raise BackupError("Backup source must be a directory.")
    files = []
    portable_paths = set()
    for current, dirs, names in os.walk(root, followlinks=False):
        base = Path(current)
        for name in dirs:
            if (base / name).is_symlink():
                raise BackupError("Backup source contains a symbolic link.")
        for name in names:
            item = base / name
            if item.is_symlink() or not item.is_file():
                raise BackupError("Backup source contains an unsupported file.")
            relative = PurePosixPath(item.relative_to(root).as_posix())
            try:
                validated = _safe_archive_path(relative.as_posix())
            except BackupError:
                raise
            portable_key = _portable_archive_key(validated)
            if portable_key in portable_paths:
                raise BackupError("Backup source contains paths that collide on a supported operating system.")
            portable_paths.add(portable_key)
            if relative.as_posix().casefold() == "yuki_backup_manifest.json":
                raise BackupError("Source contains the reserved backup manifest filename.")
            files.append(item)
    database_paths = {
        item for item in files if item.suffix.lower() in SQLITE_SUFFIXES
    }
    sqlite_sidecars = {
        Path(str(database) + suffix)
        for database in database_paths
        for suffix in ("-wal", "-shm", "-journal")
    }
    files = [item for item in files if item not in sqlite_sidecars]
    if len(files) > MAX_FILES:
        raise BackupError("Backup contains too many files.")
    return sorted(files, key=lambda p: p.relative_to(root).as_posix())


_WINDOWS_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
    *(f"COM{i}" for i in ("¹", "²", "³")),
    *(f"LPT{i}" for i in ("¹", "²", "³")),
}
_WINDOWS_FORBIDDEN = set('<>:"\\\\|?*')


def _portable_archive_key(path: PurePosixPath) -> tuple[str, ...]:
    """Normalize a validated path for case-insensitive Windows collision checks."""
    return tuple(part.casefold() for part in path.parts)


def _safe_archive_path(raw_name: str) -> PurePosixPath:
    if not isinstance(raw_name, str) or not raw_name or "\\x00" in raw_name:
        raise BackupError("Backup contains an unsafe cross-platform path.")
    # Validate raw segments before PurePosixPath normalizes away dot segments.
    if raw_name.startswith("/") or "\\\\" in raw_name:
        raise BackupError("Backup contains an unsafe cross-platform path.")
    segments = raw_name.split("/")
    if any(segment in {"", ".", ".."} for segment in segments):
        raise BackupError("Backup contains an unsafe path.")
    for segment in segments:
        if (
            segment.endswith((".", " "))
            or any(ord(char) < 32 or char in _WINDOWS_FORBIDDEN for char in segment)
        ):
            raise BackupError("Backup contains a path that is not portable across operating systems.")
        device_stem = segment.split(".", 1)[0].upper()
        if device_stem in _WINDOWS_RESERVED_NAMES:
            raise BackupError("Backup contains a reserved Windows device path.")
    return PurePosixPath(*segments)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def create_encrypted_backup(source: Path, destination: Path, passphrase: str) -> dict:
    AESGCM, Scrypt = _crypto()
    src = source.expanduser()
    dst = destination.expanduser()
    if src.is_symlink() or dst.is_symlink():
        raise BackupError("Backup source and destination must not be symbolic links.")
    root = src.resolve(strict=True)
    output = dst.resolve()
    if root == output or root in output.parents:
        raise BackupError("Backup output must be outside the source data directory.")
    files = _inventory(root)
    total = sum(p.stat().st_size for p in files)
    if total > MAX_BACKUP_BYTES:
        raise BackupError("Backup exceeds the 512 MiB uncompressed size limit.")
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise BackupError("Backup output already exists; refusing to overwrite it.")

    manifest_files = []
    with tempfile.TemporaryFile() as archive_file:
        with tarfile.open(fileobj=archive_file, mode="w:") as archive:
            for item in files:
                rel = item.relative_to(root).as_posix()
                if item.suffix.lower() in SQLITE_SUFFIXES:
                    src_db = sqlite3.connect(item.resolve().as_uri() + "?mode=ro", uri=True)
                    try:
                        # Close the temporary file handle before SQLite opens it (required on Windows).
                        with tempfile.TemporaryDirectory() as snapshot_dir:
                            snapshot_path = Path(snapshot_dir) / "snapshot.sqlite3"
                            target_db = sqlite3.connect(snapshot_path)
                            try:
                                src_db.backup(target_db)
                                check = target_db.execute("PRAGMA integrity_check").fetchone()
                                if not check or check[0] != "ok":
                                    raise BackupError(f"SQLite integrity check failed: {rel}")
                                target_db.commit()
                            finally:
                                target_db.close()
                            payload = snapshot_path.read_bytes()
                    finally:
                        src_db.close()
                else:
                    payload = item.read_bytes()
                if rel == "YUKI_BACKUP_MANIFEST.json":
                    raise BackupError("Source contains a reserved backup manifest filename.")
                digest = hashlib.sha256(payload).hexdigest()
                manifest_files.append({"path": rel, "size": len(payload), "sha256": digest})
                info = tarfile.TarInfo(name=rel)
                info.size = len(payload)
                info.mode = 0o600
                archive.addfile(info, BytesIO(payload))
                if archive_file.tell() > MAX_BACKUP_BYTES:
                    raise BackupError("Backup archive exceeds the 512 MiB limit.")
            metadata = json.dumps(
                {"schema_version": 1, "product": "Yuki Desktop", "files": manifest_files},
                sort_keys=True, separators=(",", ":"),
            ).encode("utf-8")
            info = tarfile.TarInfo(name="YUKI_BACKUP_MANIFEST.json")
            info.size = len(metadata)
            info.mode = 0o600
            archive.addfile(info, BytesIO(metadata))
        archive_file.seek(0)
        plaintext = archive_file.read(MAX_BACKUP_BYTES + 1)
    if len(plaintext) > MAX_BACKUP_BYTES:
        raise BackupError("Backup archive exceeds the 512 MiB limit.")
    salt = os.urandom(SALT_BYTES)
    nonce = os.urandom(NONCE_BYTES)
    key = _derive(passphrase, salt, Scrypt)
    encrypted = AESGCM(key).encrypt(nonce, plaintext, MAGIC)
    fd, temporary = tempfile.mkstemp(prefix=".yuki-backup-", dir=str(output.parent))
    try:
        if os.name == "posix":
            os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(MAGIC + salt + nonce + encrypted)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            # Hard-link publication is atomic and fails if another process created the target.
            os.link(temporary, output)
        except FileExistsError as exc:
            raise BackupError("Backup output appeared during creation; refusing to overwrite it.") from exc
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        raise
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
    return {"path": str(output), "file_count": len(manifest_files), "size_bytes": output.stat().st_size}


def restore_encrypted_backup(backup: Path, destination: Path, passphrase: str) -> dict:
    AESGCM, Scrypt = _crypto()
    backup_path = backup.expanduser()
    target_path = destination.expanduser()
    if backup_path.is_symlink() or target_path.is_symlink():
        raise BackupError("Backup and restore destination must not be symbolic links.")
    if not backup_path.is_file():
        raise BackupError("Backup file does not exist.")
    raw = backup_path.read_bytes()
    header_size = len(MAGIC) + SALT_BYTES + NONCE_BYTES
    if len(raw) <= header_size + 16 or not raw.startswith(MAGIC):
        raise BackupError("Backup format is invalid.")
    if len(raw) > MAX_BACKUP_BYTES + header_size + 16:
        raise BackupError("Encrypted backup exceeds the supported size limit.")
    offset = len(MAGIC)
    salt = raw[offset:offset + SALT_BYTES]
    nonce = raw[offset + SALT_BYTES:header_size]
    key = _derive(passphrase, salt, Scrypt)
    try:
        plaintext = AESGCM(key).decrypt(nonce, raw[header_size:], MAGIC)
    except Exception as exc:
        raise BackupError("Backup authentication failed; passphrase may be wrong or file altered.") from exc

    target = target_path.resolve()
    if target.exists() and (not target.is_dir() or any(target.iterdir())):
        raise BackupError("Restore destination must be empty; no existing data will be overwritten.")
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".yuki-restore-", dir=str(target.parent)) as tmp:
        stage = Path(tmp) / "data"
        stage.mkdir(mode=0o700)
        manifest = None
        seen = set()
        seen_portable = set()
        total = 0
        try:
            with tarfile.open(fileobj=BytesIO(plaintext), mode="r:") as archive:
                members = archive.getmembers()
                if len(members) > MAX_FILES + 1:
                    raise BackupError("Backup contains too many archive entries.")
                for member in members:
                    name = _safe_archive_path(member.name)
                    portable_name = _portable_archive_key(name)
                    if member.name in seen or portable_name in seen_portable:
                        raise BackupError("Backup contains duplicate or cross-platform-colliding paths.")
                    seen.add(member.name)
                    seen_portable.add(portable_name)
                    if member.name == "YUKI_BACKUP_MANIFEST.json":
                        if not member.isfile():
                            raise BackupError("Backup manifest is not a regular file.")
                        stream = archive.extractfile(member)
                        manifest = json.loads(stream.read(2 * 1024 * 1024).decode("utf-8"))
                        continue
                    if not member.isfile():
                        raise BackupError("Backup contains a non-regular file.")
                    total += member.size
                    if total > MAX_BACKUP_BYTES:
                        raise BackupError("Restored data exceeds the supported size limit.")
                    out = stage.joinpath(*name.parts)
                    out.parent.mkdir(parents=True, exist_ok=True)
                    if stage.resolve() not in out.resolve().parents:
                        raise BackupError("Backup path escapes the restore directory.")
                    source_stream = archive.extractfile(member)
                    with out.open("xb") as destination_stream:
                        shutil.copyfileobj(source_stream, destination_stream, length=1024 * 1024)
            if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
                raise BackupError("Backup manifest is missing or unsupported.")
            entries = manifest.get("files")
            if not isinstance(entries, list) or len(entries) > MAX_FILES:
                raise BackupError("Backup manifest file inventory is invalid.")
            expected = {}
            for entry in entries:
                if not isinstance(entry, dict):
                    raise BackupError("Backup manifest entry is invalid.")
                rel = entry.get("path")
                digest = entry.get("sha256")
                size = entry.get("size")
                if (
                    not isinstance(rel, str)
                    or not rel
                    or not isinstance(digest, str)
                    or len(digest) != 64
                    or any(ch not in "0123456789abcdef" for ch in digest)
                    or isinstance(size, bool)
                    or not isinstance(size, int)
                    or size < 0
                    or rel in expected
                ):
                    raise BackupError("Backup manifest entry is invalid or duplicated.")
                expected[rel] = entry
            actual = {p.relative_to(stage).as_posix(): p for p in stage.rglob("*") if p.is_file()}
            if set(expected) != set(actual):
                raise BackupError("Backup manifest does not match archived files.")
            for rel, path in actual.items():
                entry = expected[rel]
                if path.stat().st_size != entry["size"] or not hmac.compare_digest(_sha256_file(path), entry["sha256"]):
                    raise BackupError(f"Backup integrity check failed: {rel}")
                if path.suffix.lower() in SQLITE_SUFFIXES:
                    db = sqlite3.connect(path)
                    try:
                        check = db.execute("PRAGMA integrity_check").fetchone()
                        if not check or check[0] != "ok":
                            raise BackupError(f"Restored SQLite database is invalid: {rel}")
                    finally:
                        db.close()
            if target.exists():
                if any(target.iterdir()):
                    raise BackupError("Restore destination changed; refusing to overwrite data.")
                target.rmdir()
            target.parent.mkdir(parents=True, exist_ok=True)
            stage.rename(target)
            return {"destination": str(target), "file_count": len(actual), "size_bytes": total}
        except (tarfile.TarError, OSError, ValueError, KeyError, TypeError) as exc:
            if isinstance(exc, BackupError):
                raise
            raise BackupError("Backup contents are invalid; no existing data was overwritten.") from exc
