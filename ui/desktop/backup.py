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
import stat
import sqlite3
import tarfile
import tempfile
from io import BytesIO
from pathlib import Path, PurePosixPath
from typing import Mapping

MAGIC = b"YUKIBK1\0"
SALT_BYTES = 16
NONCE_BYTES = 12
MAX_BACKUP_BYTES = 512 * 1024 * 1024
MAX_FILES = 10_000
SQLITE_SUFFIXES = {".sqlite", ".sqlite3", ".db"}


class BackupError(RuntimeError):
    """Raised when a backup cannot be safely created or restored."""


def _fsync_directory(path: Path) -> None:
    """Persist directory-entry changes where directory descriptors are supported."""
    if os.name != "posix":
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _fsync_staged_tree(root: Path) -> None:
    """Flush staged regular files and directories before atomic publication.

    POSIX filesystems that honor fsync can then persist both file contents and
    the directory entries that make up the staged tree. Other platforms retain
    their native durability behavior.
    """
    if os.name != "posix":
        return
    for current, _dirs, files in os.walk(root, topdown=False, followlinks=False):
        directory = Path(current)
        for name in files:
            path = directory / name
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(path, flags)
            try:
                if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                    raise OSError("staged entry is not a regular file")
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        descriptor = os.open(directory, flags)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


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
    database_names = {item.as_posix() for item in database_paths}
    sqlite_sidecars = {
        Path(str(database) + suffix)
        for database in database_paths
        for suffix in ("-wal", "-shm", "-journal")
    }
    # Sidecars only have meaning alongside their database. An orphan WAL or
    # rollback journal may be evidence of interrupted storage activity; never
    # archive it as an ordinary file and later restore it without its database.
    for item in files:
        if item.name.endswith(("-wal", "-shm", "-journal")):
            base = item.as_posix()
            suffix = next(s for s in ("-wal", "-shm", "-journal") if base.endswith(s))
            if base[:-len(suffix)] not in database_names:
                raise BackupError("Backup source contains an orphan SQLite sidecar.")
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


def create_encrypted_backup(
    source: Path, destination: Path, passphrase: str, *,
    additional_files: Mapping[str, Path] | None = None,
    additional_roots: Mapping[str, Path] | None = None,
    memory_key_export: bytes | None = None,
) -> dict:
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
    # The backup is a restorable Yuki_HOME package: desktop state lives in .yuki/ and the configured workspace is a sibling directory.
    archive_items = [('.yuki/' + p.relative_to(root).as_posix(), p) for p in files]
    used_paths = {_portable_archive_key(_safe_archive_path(rel)) for rel, _ in archive_items}
    for prefix, additional_root in (additional_roots or {}).items():
        safe_prefix = _safe_archive_path(prefix)
        if safe_prefix.as_posix() != "workspace":
            raise BackupError("Additional backup directories must be placed under workspace/.")
        extra_root = Path(additional_root).expanduser()
        if extra_root.is_symlink():
            raise BackupError("Additional backup directory must not be a symbolic link.")
        try:
            extra_root = extra_root.resolve(strict=True)
        except OSError as exc:
            raise BackupError("An additional backup directory is unavailable.") from exc
        if extra_root == root or extra_root in root.parents or root in extra_root.parents:
            raise BackupError("Additional backup directories must be separate from the desktop profile.")
        if extra_root == output or extra_root in output.parents:
            raise BackupError("Backup output must be outside every source data directory.")
        for extra_file in _inventory(extra_root):
            relative = (safe_prefix / extra_file.relative_to(extra_root)).as_posix()
            portable_key = _portable_archive_key(_safe_archive_path(relative))
            if portable_key in used_paths:
                raise BackupError("Additional backup directory collides with an existing archive path.")
            used_paths.add(portable_key)
            archive_items.append((relative, extra_file))
    for relative, external_path in (additional_files or {}).items():
        safe_relative = _safe_archive_path(relative)
        if safe_relative.as_posix().casefold() == "yuki_backup_manifest.json":
            raise BackupError("Additional backup file cannot shadow the reserved backup manifest.")
        if not safe_relative.as_posix().startswith((".yuki/", "workspace/")):
            raise BackupError("Additional backup files must be placed under .yuki/ or workspace/.")
        portable_key = _portable_archive_key(safe_relative)
        if portable_key in used_paths:
            raise BackupError("Additional backup file collides with an existing archive path.")
        used_paths.add(portable_key)
        item = Path(external_path).expanduser()
        try:
            info = item.lstat()
        except OSError as exc:
            raise BackupError("An additional backup file is unavailable.") from exc
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise BackupError("Additional backup source must be a private regular file.")
        archive_items.append((safe_relative.as_posix(), item))
    if memory_key_export is not None:
        if not isinstance(memory_key_export, bytes) or not 1 <= len(memory_key_export) <= 2048:
            raise BackupError("Portable memory key export is invalid.")
        secret_path = ".yuki/memory_key_export.json"
        secret_key = _portable_archive_key(_safe_archive_path(secret_path))
        if secret_key in used_paths:
            raise BackupError("Portable memory key export collides with profile data.")
        used_paths.add(secret_key)
        archive_items.append((secret_path, memory_key_export))
    if len(archive_items) > MAX_FILES:
        raise BackupError("Backup contains too many files.")
    total = sum(len(item) if isinstance(item, bytes) else item.stat().st_size for _, item in archive_items)
    if total > MAX_BACKUP_BYTES:
        raise BackupError("Backup exceeds the 512 MiB uncompressed size limit.")
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        raise BackupError("Backup output already exists; refusing to overwrite it.")

    manifest_files = []
    with tempfile.TemporaryFile() as archive_file:
        with tarfile.open(fileobj=archive_file, mode="w:") as archive:
            for rel, item in archive_items:
                if isinstance(item, bytes):
                    payload = item
                elif item.suffix.lower() in SQLITE_SUFFIXES:
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
                elif not isinstance(item, bytes):
                    payload = item.read_bytes()
                if rel.casefold() == "yuki_backup_manifest.json":
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
                {"schema_version": 2, "product": "Yuki Desktop", "layout": "yuki-home", "files": manifest_files},
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
            try:
                _fsync_directory(output.parent)
            except OSError as exc:
                raise BackupError("Backup was published, but filesystem durability could not be confirmed; verify the file before retrying.") from exc
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


def restore_encrypted_backup(
    backup: Path, destination: Path, passphrase: str, *,
    keyring_module=None, import_memory_key: bool = False,
) -> dict:
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
        memory_key_export = None
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
                    if member.name == ".yuki/memory_key_export.json":
                        if not member.isfile() or member.size > 2048:
                            raise BackupError("Portable memory key export is invalid.")
                        memory_key_export = archive.extractfile(member).read(2049)
                        if len(memory_key_export) != member.size or len(memory_key_export) > 2048:
                            raise BackupError("Portable memory key export is invalid.")
                        total += member.size
                        if total > MAX_BACKUP_BYTES:
                            raise BackupError("Restored data exceeds the supported size limit.")
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
            if not isinstance(manifest, dict) or manifest.get("schema_version") not in {1, 2}:
                raise BackupError("Backup manifest is missing or unsupported.")
            schema_version = manifest["schema_version"]
            if schema_version == 2 and manifest.get("layout") != "yuki-home":
                raise BackupError("Backup profile layout is unsupported.")
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
                try:
                    rel = _safe_archive_path(rel).as_posix()
                except BackupError as exc:
                    raise BackupError("Backup manifest contains an unsafe path.") from exc
                if schema_version == 2 and not rel.startswith((".yuki/", "workspace/")):
                    raise BackupError("Backup manifest contains a path outside the Yuki profile package.")
                expected_path = rel
                if schema_version == 1 and not rel.startswith(("workspace/", ".yuki/")):
                    expected_path = ".yuki/" + rel
                if expected_path in expected:
                    raise BackupError("Backup manifest paths collide after profile normalization.")
                expected[expected_path] = entry
            # Version 1 archives stored desktop files at archive root and the
            # workspace as a sibling. Normalize them to the current YUKI_HOME
            # package layout without changing their authenticated file payloads.
            if schema_version == 1:
                desktop_root = stage / ".yuki"
                desktop_root.mkdir(mode=0o700, exist_ok=True)
                for child in tuple(stage.iterdir()):
                    if child.name in {"workspace", ".yuki"}:
                        continue
                    child.rename(desktop_root / child.name)
            if schema_version == 2:
                (stage / ".yuki").mkdir(mode=0o700, exist_ok=True)
            actual = {p.relative_to(stage).as_posix(): p for p in stage.rglob("*") if p.is_file()}
            secret_entry = expected.pop(".yuki/memory_key_export.json", None)
            if memory_key_export is not None:
                if secret_entry is None:
                    raise BackupError("Portable memory key export is missing from the authenticated manifest.")
                if (
                    len(memory_key_export) != secret_entry["size"]
                    or not hmac.compare_digest(
                        hashlib.sha256(memory_key_export).hexdigest(), secret_entry["sha256"],
                    )
                ):
                    raise BackupError("Portable memory key export failed integrity validation.")
                if not import_memory_key:
                    raise BackupError("This backup contains a portable memory key. Explicitly enable memory-key import to restore it safely.")
                if keyring_module is None:
                    raise BackupError("A secure OS keyring is required to import portable encrypted memory.")
            if set(expected) != set(actual):
                raise BackupError("Backup manifest does not match archived files.")
            if os.name == "posix":
                # Restore personal data with private permissions regardless of
                # the caller's umask or archive-supplied mode bits.
                for path in sorted(stage.rglob("*"), key=lambda item: len(item.parts), reverse=True):
                    os.chmod(path, 0o700 if path.is_dir() else 0o600)
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
            _fsync_staged_tree(stage)
            if target.exists():
                if any(target.iterdir()):
                    raise BackupError("Restore destination changed; refusing to overwrite data.")
                target.rmdir()
            target.parent.mkdir(parents=True, exist_ok=True)
            stage.rename(target)
            try:
                _fsync_directory(target.parent)
            except OSError as exc:
                raise BackupError("Restore was published, but filesystem durability could not be confirmed; verify the destination before retrying.") from exc
            return {"destination": str(target), "file_count": len(actual), "size_bytes": total}
        except (tarfile.TarError, OSError, ValueError, KeyError, TypeError) as exc:
            if isinstance(exc, BackupError):
                raise
            raise BackupError("Backup contents are invalid; no existing data was overwritten.") from exc
