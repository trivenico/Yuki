"""Content-addressed workspace snapshots with guarded restore.

Snapshots are recovery data, not an execution sandbox. The service captures only
explicitly selected regular files, rejects symlinks and sensitive paths, and
requires an application-owned authorization callback for every operation.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import secrets
import sqlite3
import stat
from typing import Callable, Iterable


class SnapshotError(RuntimeError):
    """Snapshot operation failed closed."""


_BLOCKED = frozenset({".git", ".venv", "venv", "node_modules", "__pycache__", ".ssh", ".aws", ".azure"})
_SECRET = ("secret", "credential", "password", "token", "private_key", "id_rsa", "id_ed25519")
_MAX_FILES = 200
_MAX_FILE_BYTES = 2 * 1024 * 1024
_MAX_TOTAL_BYTES = 32 * 1024 * 1024


@dataclass(frozen=True)
class SnapshotFile:
    path: str
    sha256: str
    size: int
    mode: int


@dataclass(frozen=True)
class WorkspaceSnapshot:
    snapshot_id: str
    actor_id: str
    files: tuple[SnapshotFile, ...]
    total_bytes: int
    created_at: str


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class WorkspaceSnapshotStore:
    """Immutable file snapshots with compare-and-swap restore semantics."""

    def __init__(
        self,
        workspace_root: str | Path,
        storage_root: str | Path,
        *,
        authorize: Callable[[str, str, str], bool],
    ):
        self.workspace = Path(workspace_root).expanduser().resolve(strict=True)
        self.storage = Path(storage_root).expanduser().resolve()
        if not self.workspace.is_dir():
            raise ValueError("workspace_root must be a directory.")
        if self.storage == self.workspace or self.workspace in self.storage.parents:
            raise ValueError("snapshot storage must be outside the workspace.")
        if not callable(authorize):
            raise ValueError("authorize callback is required.")
        self.authorize = authorize
        self.blobs = self.storage / "blobs"
        self.blobs.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.storage.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.database = self.storage / "snapshots.sqlite3"
        with self._connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=FULL")
            db.execute("""CREATE TABLE IF NOT EXISTS snapshots (
                snapshot_id TEXT PRIMARY KEY, actor_id TEXT NOT NULL,
                created_at TEXT NOT NULL, total_bytes INTEGER NOT NULL)""")
            db.execute("""CREATE TABLE IF NOT EXISTS snapshot_files (
                snapshot_id TEXT NOT NULL, path TEXT NOT NULL, sha256 TEXT NOT NULL,
                size INTEGER NOT NULL, mode INTEGER NOT NULL,
                PRIMARY KEY(snapshot_id,path),
                FOREIGN KEY(snapshot_id) REFERENCES snapshots(snapshot_id))""")

    def _connect(self):
        db = sqlite3.connect(self.database, timeout=5.0)
        db.execute("PRAGMA busy_timeout=5000")
        db.execute("PRAGMA foreign_keys=ON")
        return db

    def _allowed(self, actor: str, action: str) -> None:
        try:
            allowed = self.authorize(actor, action, str(self.workspace)) is True
        except Exception:
            allowed = False
        if not isinstance(actor, str) or not actor.strip() or not allowed:
            raise PermissionError("snapshot authorization denied.")

    def _path(self, relative: str) -> Path:
        if not isinstance(relative, str) or not relative.strip() or len(relative) > 1000 or "\x00" in relative:
            raise SnapshotError("file path must be bounded relative text.")
        rel = Path(relative)
        if rel.is_absolute() or any(part in {"", ".."} for part in rel.parts):
            raise SnapshotError("absolute paths and traversal are not allowed.")
        if any(part in _BLOCKED or any(word in part.lower() for word in _SECRET) or part.lower().startswith(".env") for part in rel.parts):
            raise SnapshotError("sensitive or generated paths are excluded.")
        target = self.workspace
        for part in rel.parts:
            target = target / part
            if target.is_symlink():
                raise SnapshotError("symlink paths are not supported.")
        try:
            resolved = target.resolve(strict=True)
            resolved.relative_to(self.workspace)
        except (OSError, RuntimeError, ValueError) as exc:
            raise SnapshotError("path must exist inside the workspace.") from exc
        info = resolved.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_size > _MAX_FILE_BYTES:
            raise SnapshotError("only bounded regular files can be snapshotted.")
        return resolved

    def create(self, *, actor_id: str, paths: Iterable[str]) -> WorkspaceSnapshot:
        self._allowed(actor_id, "workspace:snapshot")
        selected = list(paths)
        if not selected or len(selected) > _MAX_FILES or len(set(selected)) != len(selected):
            raise SnapshotError("provide 1 to 200 unique file paths.")
        staged = []
        total = 0
        for relative in selected:
            path = self._path(relative)
            data = path.read_bytes()
            if len(data) > _MAX_FILE_BYTES:
                raise SnapshotError("file exceeds snapshot size limit.")
            total += len(data)
            if total > _MAX_TOTAL_BYTES:
                raise SnapshotError("snapshot exceeds total size limit.")
            mode = stat.S_IMODE(path.stat().st_mode)
            digest = _digest(data)
            staged.append((relative, data, digest, mode))
        snapshot_id = "snp_" + secrets.token_hex(12)
        from datetime import datetime, timezone
        created = datetime.now(timezone.utc).isoformat()
        # Write immutable blobs before publishing the manifest. A crash can leave
        # harmless orphan blobs, but never a manifest pointing at a partial blob.
        for _, data, digest, _ in staged:
            blob = self.blobs / digest
            if not blob.exists():
                temp = self.blobs / (".tmp-" + secrets.token_hex(8))
                fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                try:
                    with os.fdopen(fd, "wb") as stream:
                        stream.write(data)
                        stream.flush()
                        os.fsync(stream.fileno())
                    try:
                        os.link(temp, blob)
                    except FileExistsError:
                        pass
                finally:
                    temp.unlink(missing_ok=True)
        with self._connect() as db:
            db.execute("INSERT INTO snapshots VALUES (?, ?, ?, ?)", (snapshot_id, actor_id, created, total))
            db.executemany("INSERT INTO snapshot_files VALUES (?, ?, ?, ?, ?)",
                [(snapshot_id, p, digest, len(data), mode) for p, data, digest, mode in staged])
        return WorkspaceSnapshot(snapshot_id, actor_id,
            tuple(SnapshotFile(p, digest, len(data), mode) for p, data, digest, mode in staged),
            total, created)

    def get(self, *, actor_id: str, snapshot_id: str) -> WorkspaceSnapshot:
        self._allowed(actor_id, "workspace:snapshot:read")
        with self._connect() as db:
            row = db.execute("SELECT actor_id,created_at,total_bytes FROM snapshots WHERE snapshot_id=?", (snapshot_id,)).fetchone()
            if row is None:
                raise KeyError("snapshot not found.")
            if row[0] != actor_id:
                raise PermissionError("snapshot belongs to another actor.")
            files = db.execute("SELECT path,sha256,size,mode FROM snapshot_files WHERE snapshot_id=? ORDER BY path", (snapshot_id,)).fetchall()
        return WorkspaceSnapshot(snapshot_id, row[0],
            tuple(SnapshotFile(*item) for item in files), row[2], row[1])

    def restore(
        self, *, actor_id: str, snapshot_id: str,
        expected_current: dict[str, str],
    ) -> WorkspaceSnapshot:
        """Restore only if every current file matches the caller's expected hash."""
        self._allowed(actor_id, "workspace:restore")
        snapshot = self.get(actor_id=actor_id, snapshot_id=snapshot_id)
        if not isinstance(expected_current, dict) or set(expected_current) != {f.path for f in snapshot.files}:
            raise SnapshotError("expected_current must contain exactly the snapshotted paths.")
        staged = []
        for item in snapshot.files:
            target = self._path(item.path)
            current = target.read_bytes()
            if _digest(current) != expected_current[item.path]:
                raise SnapshotError("workspace changed since restore was prepared; refusing overwrite.")
            blob = self.blobs / item.sha256
            data = blob.read_bytes()
            if len(data) != item.size or _digest(data) != item.sha256:
                raise SnapshotError("snapshot blob failed integrity verification.")
            staged.append((target, data, item.mode))
        # Prepare all temporary files first. Replace only after every blob and
        # compare-and-swap check succeeds.
        temps = []
        try:
            for target, data, mode in staged:
                temp = target.with_name("." + target.name + ".yuki-" + secrets.token_hex(8))
                fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
                with os.fdopen(fd, "wb") as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                temps.append((temp, target))
            for temp, target in temps:
                os.replace(temp, target)
        except Exception as exc:
            for temp, _ in temps:
                temp.unlink(missing_ok=True)
            raise SnapshotError("restore failed; inspect workspace state before retrying.") from exc
        return snapshot
