"""Crash-recoverable journal for explicitly approved workspace file transactions.

The journal lives outside the editable workspace. It provides deterministic
rollback after process death; it does NOT make multiple filesystem renames
atomically visible as one operation.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import time
import uuid
from typing import Sequence


class WorkspaceJournalError(RuntimeError):
    """A journal is corrupt, unsafe to recover, or could not be made durable."""


def _fsync_directory(path: Path) -> None:
    # POSIX permits directory fsync for rename durability. Windows does not
    # expose an equivalent through Python's portable file API.
    if os.name == "nt":
        return
    fd = os.open(str(path), os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class WorkspaceTransactionJournal:
    """Persist backups and staged replacements before the first target rename."""

    def __init__(self, workspace: str | Path, journal_dir: str | Path | None = None):
        self.root = Path(workspace).expanduser().resolve(strict=True)
        if not self.root.is_dir():
            raise ValueError("workspace must be an existing directory")
        if journal_dir is None:
            identity = hashlib.sha256(os.fsencode(str(self.root))).hexdigest()[:24]
            journal_dir = Path(tempfile.gettempdir()) / "yuki-private-workspace-journals" / identity
        raw = Path(journal_dir).expanduser()
        raw.mkdir(parents=True, exist_ok=True, mode=0o700)
        if raw.is_symlink():
            raise WorkspaceJournalError("journal directory must not be a symlink")
        self.directory = raw.resolve(strict=True)
        try:
            self.directory.relative_to(self.root)
        except ValueError:
            pass
        else:
            raise WorkspaceJournalError("journal directory must be outside the editable workspace")
        os.chmod(self.directory, 0o700)
        self._fsync_directory(self.directory.parent)

    @staticmethod
    def _fsync_directory(path: Path) -> None:
        _fsync_directory(path)

    def _write_manifest(self, operation_dir: Path, manifest: dict) -> None:
        payload = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
        fd, temp_name = tempfile.mkstemp(prefix=".manifest-", dir=str(operation_dir))
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temp_name, 0o600)
            os.replace(temp_name, operation_dir / "manifest.json")
            _fsync_directory(operation_dir)
        except Exception:
            try:
                os.unlink(temp_name)
            except OSError:
                pass
            raise

    def begin(self, *, actor_id: str, operation_digest: str, changes: Sequence[tuple[str, Path, bytes, bytes]]) -> dict:
        if not isinstance(actor_id, str) or not actor_id or len(actor_id) > 256:
            raise ValueError("actor_id must be bounded non-empty text")
        if not isinstance(operation_digest, str) or len(operation_digest) != 64:
            raise ValueError("operation_digest must be a SHA-256 digest")
        operation_id = uuid.uuid4().hex
        operation_dir = self.directory / operation_id
        operation_dir.mkdir(mode=0o700)
        entries = []
        try:
            for index, (relative, target, old, new) in enumerate(changes):
                resolved = target.resolve(strict=True)
                try:
                    resolved.relative_to(self.root)
                except ValueError as exc:
                    raise WorkspaceJournalError("transaction target escaped workspace") from exc
                if not resolved.is_file() or resolved.is_symlink():
                    raise WorkspaceJournalError("transaction target must be a regular non-symlink file")
                if _sha256(old) != _sha256(resolved.read_bytes()):
                    raise WorkspaceJournalError("transaction target changed before journal preparation")
                backup_name = f"backup-{index:02d}.bin"
                backup_path = operation_dir / backup_name
                fd = os.open(str(backup_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "wb") as handle:
                    handle.write(old)
                    handle.flush()
                    os.fsync(handle.fileno())
                fd, stage_name = tempfile.mkstemp(prefix=".yuki-stage-", dir=str(resolved.parent))
                with os.fdopen(fd, "wb") as handle:
                    handle.write(new)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.chmod(stage_name, resolved.stat().st_mode & 0o777)
                entries.append({
                    "path": relative,
                    "target": str(resolved),
                    "backup": backup_name,
                    "stage": stage_name,
                    "old_sha256": _sha256(old),
                    "new_sha256": _sha256(new),
                    "mode": resolved.stat().st_mode & 0o777,
                })
            _fsync_directory(operation_dir)
            manifest = {
                "schema_version": 1,
                "operation_id": operation_id,
                "workspace": str(self.root),
                "actor_id": actor_id,
                "operation_digest": operation_digest,
                "state": "prepared",
                "created_at": time.time(),
                "updated_at": time.time(),
                "entries": entries,
                "applied_paths": [],
            }
            self._write_manifest(operation_dir, manifest)
            _fsync_directory(self.directory)
            return manifest
        except Exception:
            for entry in entries:
                try:
                    Path(entry["stage"]).unlink(missing_ok=True)
                except OSError:
                    pass
            shutil.rmtree(operation_dir, ignore_errors=True)
            raise

    def _load(self, operation_dir: Path) -> dict:
        if operation_dir.is_symlink() or not operation_dir.is_dir():
            raise WorkspaceJournalError("unsafe journal operation directory")
        path = operation_dir / "manifest.json"
        if path.is_symlink() or not path.is_file():
            raise WorkspaceJournalError("journal manifest is missing or unsafe")
        try:
            manifest = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise WorkspaceJournalError("journal manifest is unreadable") from exc
        if (not isinstance(manifest, dict) or manifest.get("schema_version") != 1
                or manifest.get("workspace") != str(self.root)
                or manifest.get("operation_id") != operation_dir.name
                or not isinstance(manifest.get("entries"), list)):
            raise WorkspaceJournalError("journal manifest identity or schema is invalid")
        return manifest

    def _save(self, operation_dir: Path, manifest: dict, state: str | None = None) -> None:
        if state is not None:
            manifest["state"] = state
        manifest["updated_at"] = time.time()
        self._write_manifest(operation_dir, manifest)

    def mark_applying(self, operation_id: str) -> None:
        operation_dir = self.directory / operation_id
        manifest = self._load(operation_dir)
        if manifest["state"] != "prepared":
            raise WorkspaceJournalError("transaction is not prepared")
        self._save(operation_dir, manifest, "applying")

    def record_replacement(self, operation_id: str, relative_path: str) -> None:
        operation_dir = self.directory / operation_id
        manifest = self._load(operation_dir)
        if manifest["state"] != "applying":
            raise WorkspaceJournalError("transaction is not applying")
        if relative_path not in [item["path"] for item in manifest["entries"]]:
            raise WorkspaceJournalError("replacement path is not in the journal")
        if relative_path not in manifest["applied_paths"]:
            manifest["applied_paths"].append(relative_path)
        self._save(operation_dir, manifest)

    def mark_committed(self, operation_id: str) -> None:
        operation_dir = self.directory / operation_id
        manifest = self._load(operation_dir)
        if manifest["state"] != "applying":
            raise WorkspaceJournalError("only an applying transaction can commit")
        for entry in manifest["entries"]:
            target = Path(entry["target"])
            if target.is_symlink() or not target.is_file() or _sha256(target.read_bytes()) != entry["new_sha256"]:
                raise WorkspaceJournalError("cannot commit: target does not match staged digest")
        self._save(operation_dir, manifest, "committed")

    def _restore(self, operation_dir: Path, manifest: dict) -> None:
        entries = manifest["entries"]
        for entry in reversed(entries):
            target = Path(entry["target"])
            try:
                target.relative_to(self.root)
            except ValueError as exc:
                raise WorkspaceJournalError("recovery target escaped workspace") from exc
            if target.is_symlink() or not target.parent.is_dir():
                raise WorkspaceJournalError("recovery target path is unsafe")
            cursor = self.root
            try:
                for part in target.relative_to(self.root).parts:
                    cursor = cursor / part
                    if cursor.is_symlink():
                        raise WorkspaceJournalError("recovery path contains a symlink")
                target.parent.resolve(strict=True).relative_to(self.root)
            except (OSError, RuntimeError, ValueError) as exc:
                raise WorkspaceJournalError("recovery path escaped the workspace") from exc
            backup = operation_dir / entry["backup"]
            if backup.parent != operation_dir or backup.is_symlink() or not backup.is_file():
                raise WorkspaceJournalError("recovery backup is missing or unsafe")
            old = backup.read_bytes()
            if _sha256(old) != entry["old_sha256"]:
                raise WorkspaceJournalError("recovery backup digest mismatch")
            if target.exists():
                current = _sha256(target.read_bytes())
                if current == entry["old_sha256"]:
                    pass
                elif current == entry["new_sha256"]:
                    fd, restore_name = tempfile.mkstemp(prefix=".yuki-restore-", dir=str(target.parent))
                    with os.fdopen(fd, "wb") as handle:
                        handle.write(old)
                        handle.flush()
                        os.fsync(handle.fileno())
                    os.chmod(restore_name, entry["mode"])
                    os.replace(restore_name, target)
                    _fsync_directory(target.parent)
                else:
                    raise WorkspaceJournalError("recovery conflict: target matches neither old nor approved content")
            else:
                raise WorkspaceJournalError("recovery conflict: approved target disappeared")
        for entry in entries:
            stage = Path(entry["stage"])
            if stage.parent != Path(entry["target"]).parent or not stage.name.startswith(".yuki-stage-"):
                raise WorkspaceJournalError("staged path is unsafe")
            stage.unlink(missing_ok=True)
        self._save(operation_dir, manifest, "rolled_back")

    def rollback(self, operation_id: str) -> None:
        if not isinstance(operation_id, str) or len(operation_id) != 32:
            raise WorkspaceJournalError("invalid operation identifier")
        operation_dir = self.directory / operation_id
        manifest = self._load(operation_dir)
        if manifest["state"] in {"committed", "rolled_back"}:
            return
        if manifest["state"] not in {"prepared", "applying", "recovery_required"}:
            raise WorkspaceJournalError("unknown journal state requires operator review")
        try:
            self._restore(operation_dir, manifest)
        except Exception:
            self._save(operation_dir, manifest, "recovery_required")
            raise

    def recover_pending(self) -> list[str]:
        recovered = []
        for operation_dir in sorted(self.directory.iterdir()):
            if operation_dir.is_symlink() or not operation_dir.is_dir():
                raise WorkspaceJournalError("unexpected entry in private journal directory")
            manifest = self._load(operation_dir)
            if manifest["state"] in {"committed", "rolled_back"}:
                continue
            if manifest["state"] not in {"prepared", "applying", "recovery_required"}:
                raise WorkspaceJournalError("unknown journal state requires operator review")
            try:
                self._restore(operation_dir, manifest)
            except Exception:
                self._save(operation_dir, manifest, "recovery_required")
                raise
            recovered.append(manifest["operation_id"])
        return recovered
