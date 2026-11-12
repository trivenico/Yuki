"""Tenant-isolated, quarantined media storage for annotation assets.

Uploads are opaque bytes, never caller-supplied paths or URLs. New assets remain
unavailable until an explicitly configured scanner marks them clean.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
import secrets
import sqlite3


_ASSET_ID = re.compile(r"^[0-9a-f]{32}$")
_OWNER_MAX = 256
_DEFAULT_MAX_BYTES = 25 * 1024 * 1024
_MEDIA_SIGNATURES = (
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
    (b"RIFF", "image/webp"),
    (b"RIFF", "audio/wav"),
    (b"ID3", "audio/mpeg"),
    (b"\xff\xfb", "audio/mpeg"),
    (b"\xff\xf3", "audio/mpeg"),
    (b"\xff\xf2", "audio/mpeg"),
    (b"\x00\x00\x00", "video/mp4"),
)


@dataclass(frozen=True)
class AnnotationAsset:
    asset_id: str
    owner_id: str
    project_id: str
    media_type: str
    size_bytes: int
    sha256: str
    scan_status: str
    created_at: str


class AnnotationAssetError(ValueError):
    """Invalid or unavailable annotation asset."""


class SQLiteAnnotationAssetStore:
    """Private local media store. All new assets are quarantined by default."""

    def __init__(self, root: str, database_path: str, *, max_bytes: int = _DEFAULT_MAX_BYTES):
        if type(max_bytes) is not int or not 1 <= max_bytes <= 250 * 1024 * 1024:
            raise ValueError("invalid_asset_size_limit")
        root_path = Path(root).expanduser()
        db_path = Path(database_path).expanduser()
        if root_path.is_symlink() or db_path.is_symlink():
            raise ValueError("asset_storage_paths_must_not_be_symlinks")
        root_path.mkdir(parents=True, exist_ok=True, mode=0o700)
        db_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.root = root_path.resolve()
        self.database_path = db_path.resolve()
        self.max_bytes = max_bytes
        if os.name == "posix":
            os.chmod(self.root, 0o700)
            os.chmod(self.database_path.parent, 0o700)
        with self._connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("""CREATE TABLE IF NOT EXISTS annotation_assets (
                asset_id TEXT PRIMARY KEY,
                owner_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                media_type TEXT NOT NULL,
                size_bytes INTEGER NOT NULL,
                sha256 TEXT NOT NULL,
                scan_status TEXT NOT NULL CHECK(scan_status IN ('quarantined','clean','blocked','deleted')),
                created_at TEXT NOT NULL,
                UNIQUE(owner_id, project_id, asset_id)
            )""")
            db.execute("""CREATE INDEX IF NOT EXISTS idx_annotation_assets_owner
                ON annotation_assets(owner_id, project_id, scan_status)""")
        if os.name == "posix" and self.database_path.exists():
            os.chmod(self.database_path, 0o600)

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.database_path, timeout=5.0, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=5000")
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _validate_scope(owner_id: str, project_id: str) -> None:
        if not isinstance(owner_id, str) or not owner_id.strip() or len(owner_id) > _OWNER_MAX:
            raise AnnotationAssetError("invalid_owner_id")
        if not isinstance(project_id, str) or not re.fullmatch(r"[0-9a-f]{32}", project_id):
            raise AnnotationAssetError("invalid_project_id")

    @staticmethod
    def _detect_media_type(data: bytes) -> str:
        if data.startswith(b"RIFF") and len(data) >= 12:
            if data[8:12] == b"WEBP":
                return "image/webp"
            if data[8:12] == b"WAVE":
                return "audio/wav"
        if data.startswith(b"\x00\x00\x00") and len(data) >= 12 and data[4:8] == b"ftyp":
            return "video/mp4"
        for signature, media_type in _MEDIA_SIGNATURES:
            if data.startswith(signature):
                return media_type
        raise AnnotationAssetError("unsupported_or_unrecognized_media_type")

    def _asset_path(self, owner_id: str, asset_id: str) -> Path:
        owner_key = hashlib.sha256(owner_id.encode("utf-8")).hexdigest()
        directory = self.root / owner_key
        directory.mkdir(mode=0o700, exist_ok=True)
        if directory.is_symlink() or directory.resolve().parent != self.root:
            raise AnnotationAssetError("unsafe_asset_directory")
        return directory / asset_id

    def upload(self, owner_id: str, project_id: str, data: bytes) -> AnnotationAsset:
        self._validate_scope(owner_id, project_id)
        if not isinstance(data, bytes) or not data or len(data) > self.max_bytes:
            raise AnnotationAssetError("invalid_or_oversized_asset")
        media_type = self._detect_media_type(data)
        asset_id = secrets.token_hex(16)
        path = self._asset_path(owner_id, asset_id)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        fd = os.open(path, flags, 0o600)
        try:
            with os.fdopen(fd, "wb") as output:
                output.write(data)
                output.flush()
                os.fsync(output.fileno())
            digest = hashlib.sha256(data).hexdigest()
            from datetime import datetime, timezone
            created_at = datetime.now(timezone.utc).isoformat()
            with self._connect() as db:
                db.execute("BEGIN IMMEDIATE")
                db.execute("""INSERT INTO annotation_assets
                    (asset_id, owner_id, project_id, media_type, size_bytes, sha256, scan_status, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, 'quarantined', ?)""",
                    (asset_id, owner_id, project_id, media_type, len(data), digest, created_at))
            return AnnotationAsset(asset_id, owner_id, project_id, media_type,
                                   len(data), digest, "quarantined", created_at)
        except BaseException:
            try:
                path.unlink(missing_ok=True)
            finally:
                raise

    def mark_scan_result(self, owner_id: str, project_id: str, asset_id: str, *, clean: bool) -> bool:
        self._validate_scope(owner_id, project_id)
        if not isinstance(asset_id, str) or not _ASSET_ID.fullmatch(asset_id) or type(clean) is not bool:
            raise AnnotationAssetError("invalid_scan_result")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            cursor = db.execute("""UPDATE annotation_assets SET scan_status=?
                WHERE owner_id=? AND project_id=? AND asset_id=? AND scan_status='quarantined'""",
                ("clean" if clean else "blocked", owner_id, project_id, asset_id))
            return cursor.rowcount == 1

    def get_asset(self, owner_id: str, project_id: str, asset_id: str) -> AnnotationAsset | None:
        self._validate_scope(owner_id, project_id)
        if not isinstance(asset_id, str) or not _ASSET_ID.fullmatch(asset_id):
            return None
        with self._connect() as db:
            row = db.execute("""SELECT * FROM annotation_assets
                WHERE owner_id=? AND project_id=? AND asset_id=? AND scan_status='clean'""",
                (owner_id, project_id, asset_id)).fetchone()
        return AnnotationAsset(**dict(row)) if row else None

    def read(self, owner_id: str, project_id: str, asset_id: str) -> bytes:
        asset = self.get_asset(owner_id, project_id, asset_id)
        if asset is None:
            raise LookupError("annotation_asset_not_found")
        path = self._asset_path(owner_id, asset_id)
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        fd = os.open(path, flags)
        try:
            with os.fdopen(fd, "rb") as source:
                data = source.read(self.max_bytes + 1)
        except OSError:
            raise LookupError("annotation_asset_not_found") from None
        if len(data) != asset.size_bytes or hashlib.sha256(data).hexdigest() != asset.sha256:
            raise AnnotationAssetError("asset_integrity_check_failed")
        return data

    def delete(self, owner_id: str, project_id: str, asset_id: str) -> bool:
        self._validate_scope(owner_id, project_id)
        if not isinstance(asset_id, str) or not _ASSET_ID.fullmatch(asset_id):
            return False
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("""SELECT scan_status FROM annotation_assets
                WHERE owner_id=? AND project_id=? AND asset_id=?""",
                (owner_id, project_id, asset_id)).fetchone()
            if row is None or row["scan_status"] == "deleted":
                return False
            db.execute("""UPDATE annotation_assets SET scan_status='deleted'
                WHERE owner_id=? AND project_id=? AND asset_id=?""",
                (owner_id, project_id, asset_id))
        path = self._asset_path(owner_id, asset_id)
        try:
            path.unlink(missing_ok=True)
        except OSError:
            # Metadata stays deleted; a later garbage collector can retry physical removal.
            raise AnnotationAssetError("asset_delete_cleanup_required") from None
        return True
