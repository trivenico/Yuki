"""Metadata-only, quarantined catalogue for imported third-party skill guidance.

This module never imports or executes skill code. Imported content remains
quarantined until a human reviewer records security and license review, then
explicitly enables it. Runtime loading is intentionally a separate later step.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any

from core.portable_paths import data_root
from core.skill_packs import read_skill_pack

_ID_RE = re.compile(r"^[a-z][a-z0-9_.-]{1,95}$")
_COMMIT_RE = re.compile(r"^[0-9a-f]{40,64}$")
_PATH_RE = re.compile(r"^[A-Za-z0-9_.@/-]{1,240}$")
_ALLOWED_STATES = {"quarantined", "reviewed", "enabled", "disabled", "revoked"}
_LOCK = RLock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_catalog_json_key")
        result[key] = value
    return result


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_pairs)
    except (OSError, UnicodeError, json.JSONDecodeError, RecursionError, ValueError) as exc:
        raise ValueError("catalog_record_invalid") from exc
    if not isinstance(value, dict):
        raise ValueError("catalog_record_must_be_object")
    return value


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".skill-catalog-", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        # Per-record writes are serialized in-process and atomically published.
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


class ExternalSkillCatalog:
    """Local metadata registry; it does not grant runtime execution capability."""

    def __init__(self, directory: str | Path | None = None):
        self.directory = Path(directory).expanduser().resolve() if directory else (
            data_root() / "external_skill_catalog"
        ).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)

    def _path(self, skill_id: str) -> Path:
        if not isinstance(skill_id, str) or not _ID_RE.fullmatch(skill_id):
            raise ValueError("invalid_external_skill_id")
        filename = skill_id.replace(".", "__") + ".json"
        path = (self.directory / filename).resolve()
        if path.parent != self.directory:
            raise ValueError("invalid_catalog_path")
        return path

    def stage(
        self,
        source_file: str | Path,
        *,
        skill_id: str,
        repository: str,
        source_path: str,
        commit: str,
        license_id: str = "UNKNOWN",
    ) -> dict[str, Any]:
        """Parse metadata/content and place it in quarantine; never run scripts."""
        path = Path(source_file).expanduser()
        if path.is_symlink() or not path.is_file():
            raise ValueError("skill_source_must_be_regular_non_symlink_file")
        raw = path.read_bytes()
        if len(raw) > 64 * 1024:
            raise ValueError("skill_source_exceeds_64KiB")
        if not isinstance(repository, str) or not re.fullmatch(
            r"[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}", repository
        ):
            raise ValueError("invalid_source_repository")
        if not isinstance(source_path, str) or not _PATH_RE.fullmatch(source_path):
            raise ValueError("invalid_source_path")
        parts = Path(source_path).parts
        if source_path.startswith("/") or any(part in {".", ".."} for part in parts):
            raise ValueError("source_path_must_be_relative_and_contained")
        if not isinstance(commit, str) or not _COMMIT_RE.fullmatch(commit):
            raise ValueError("source_commit_must_be_immutable_git_sha")
        if not isinstance(license_id, str) or not 1 <= len(license_id) <= 80:
            raise ValueError("invalid_license_identifier")
        manifest = read_skill_pack(path)
        digest = hashlib.sha256(raw).hexdigest()
        now = _now()
        record = {
            "schema_version": 1,
            "skill_id": skill_id,
            "name": manifest["name"],
            "description": manifest["description"],
            "instructions": manifest["instructions"],
            "risk": "high",
            "upstream_metadata": manifest.get("upstream_metadata", {}),
            "source": {
                "repository": repository,
                "path": source_path,
                "commit": commit.lower(),
                "content_sha256": digest,
                "format": manifest.get("source_format", "json"),
                "license": license_id,
            },
            "trust_state": "quarantined",
            "created_at": now,
            "updated_at": now,
            "review": None,
            "audit": [{"action": "staged", "actor": "system", "at": now}],
        }
        target = self._path(skill_id)
        with _LOCK:
            if target.exists():
                existing = _read_json(target)
                if existing.get("source", {}).get("content_sha256") == digest:
                    return existing
                raise FileExistsError("external_skill_id_already_exists")
            _atomic_json(target, record)
        return record

    def list(self, *, trust_state: str | None = None) -> list[dict[str, Any]]:
        if trust_state is not None and trust_state not in _ALLOWED_STATES:
            raise ValueError("invalid_trust_state")
        records = []
        if not self.directory.exists():
            return records
        for path in sorted(self.directory.glob("*.json")):
            try:
                record = _read_json(path)
                if record.get("schema_version") != 1 or record.get("trust_state") not in _ALLOWED_STATES:
                    continue
                if trust_state is None or record["trust_state"] == trust_state:
                    # Listing is metadata-only; instructions are not needed by callers.
                    records.append({key: value for key, value in record.items() if key != "instructions"})
            except ValueError:
                continue
        return records

    def get(self, skill_id: str) -> dict[str, Any]:
        return _read_json(self._path(skill_id))

    def review(
        self,
        skill_id: str,
        *,
        reviewer: str,
        security_reviewed: bool,
        license_reviewed: bool,
        note: str,
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Mark a quarantined import reviewed; no execution is enabled."""
        if confirm is not True:
            raise PermissionError("explicit_review_confirmation_required")
        if not isinstance(reviewer, str) or not reviewer.strip() or len(reviewer) > 160:
            raise ValueError("reviewer_identity_required")
        if security_reviewed is not True or license_reviewed is not True:
            raise PermissionError("security_and_license_review_required")
        if not isinstance(note, str) or not note.strip() or len(note) > 2000:
            raise ValueError("bounded_review_note_required")
        with _LOCK:
            record = self.get(skill_id)
            if record.get("trust_state") != "quarantined":
                raise ValueError("only_quarantined_skills_can_be_reviewed")
            record["trust_state"] = "reviewed"
            record["review"] = {
                "reviewer": reviewer.strip(),
                "security_reviewed": True,
                "license_reviewed": True,
                "note": note.strip(),
                "at": _now(),
            }
            self._audit(record, "reviewed", reviewer.strip())
            _atomic_json(self._path(skill_id), record)
            return record

    def enable(self, skill_id: str, *, actor: str, confirm: bool = False) -> dict[str, Any]:
        """Explicitly enable a reviewed skill; does not execute or load it."""
        if confirm is not True:
            raise PermissionError("explicit_enable_confirmation_required")
        if not isinstance(actor, str) or not actor.strip() or len(actor) > 160:
            raise ValueError("actor_identity_required")
        with _LOCK:
            record = self.get(skill_id)
            if record.get("trust_state") != "reviewed" or not isinstance(record.get("review"), dict):
                raise PermissionError("skill_requires_completed_review")
            record["trust_state"] = "enabled"
            self._audit(record, "enabled", actor.strip())
            _atomic_json(self._path(skill_id), record)
            return record

    def disable(self, skill_id: str, *, actor: str, confirm: bool = False) -> dict[str, Any]:
        return self._change_state(skill_id, "disabled", actor=actor, confirm=confirm,
                                  allowed={"enabled", "reviewed"})

    def revoke(self, skill_id: str, *, actor: str, confirm: bool = False) -> dict[str, Any]:
        return self._change_state(skill_id, "revoked", actor=actor, confirm=confirm,
                                  allowed={"enabled", "disabled", "reviewed", "quarantined"})

    def _change_state(self, skill_id, target, *, actor, confirm, allowed):
        if confirm is not True:
            raise PermissionError("explicit_state_change_confirmation_required")
        if not isinstance(actor, str) or not actor.strip() or len(actor) > 160:
            raise ValueError("actor_identity_required")
        with _LOCK:
            record = self.get(skill_id)
            if record.get("trust_state") not in allowed:
                raise ValueError("invalid_skill_lifecycle_transition")
            record["trust_state"] = target
            self._audit(record, target, actor.strip())
            _atomic_json(self._path(skill_id), record)
            return record

    @staticmethod
    def _audit(record, action, actor):
        record.setdefault("audit", []).append({"action": action, "actor": actor, "at": _now()})
        record["updated_at"] = _now()
