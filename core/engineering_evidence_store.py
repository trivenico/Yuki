"""Append-only, hash-chained storage for engineering check evidence snapshots.

This store preserves what Yuki observed; it does not authenticate a human approver or
replace GitHub branch protection. Keep tokens and other secrets out of the payload.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import hmac
import json
from pathlib import Path
import re
import sqlite3
from typing import Any

_ZERO_HASH = "0" * 64
_MAX_PAYLOAD_BYTES = 1_000_000
_SHA_RE = re.compile(r"^[0-9a-fA-F]{40}$")
_REPO_PART_RE = re.compile(r"^[A-Za-z0-9_.-]{1,100}$")


class EngineeringEvidenceError(ValueError):
    """Engineering evidence is invalid, too large, or fails integrity checks."""


class SQLiteEngineeringEvidenceStore:
    """Store bounded evidence snapshots in a transactionally appended hash chain."""

    def __init__(self, database_path: str):
        if not isinstance(database_path, str) or not database_path.strip():
            raise ValueError("database_path must be a non-empty path")
        self.database_path = str(Path(database_path).expanduser())
        Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS engineering_evidence (
                    evidence_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    repository TEXT NOT NULL,
                    pr_number INTEGER NOT NULL,
                    head_sha TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    payload_sha256 TEXT NOT NULL,
                    previous_hash TEXT NOT NULL,
                    event_hash TEXT NOT NULL
                )
            """)
            db.execute("""
                CREATE INDEX IF NOT EXISTS idx_engineering_evidence_pr
                ON engineering_evidence(repository, pr_number, evidence_id)
            """)
            db.execute("""
                CREATE TRIGGER IF NOT EXISTS engineering_evidence_no_update
                BEFORE UPDATE ON engineering_evidence
                BEGIN SELECT RAISE(ABORT, 'engineering evidence is append-only'); END
            """)
            db.execute("""
                CREATE TRIGGER IF NOT EXISTS engineering_evidence_no_delete
                BEFORE DELETE ON engineering_evidence
                BEGIN SELECT RAISE(ABORT, 'engineering evidence is append-only'); END
            """)

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.database_path, timeout=5.0, isolation_level=None)
        try:
            db.execute("PRAGMA busy_timeout = 5000")
            db.execute("PRAGMA foreign_keys = ON")
            yield db
        finally:
            db.close()

    @staticmethod
    def _canonical_json(payload: dict[str, Any]) -> str:
        if not isinstance(payload, dict):
            raise EngineeringEvidenceError("payload must be a JSON object")
        try:
            encoded = json.dumps(
                payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                allow_nan=False,
            )
        except (TypeError, ValueError) as exc:
            raise EngineeringEvidenceError("payload must contain only finite JSON values") from exc
        if len(encoded.encode("utf-8")) > _MAX_PAYLOAD_BYTES:
            raise EngineeringEvidenceError("payload exceeds the 1 MB limit")
        return encoded

    @staticmethod
    def _event_hash(repository: str, pr_number: int, head_sha: str,
                    observed_at: str, payload_sha256: str, previous_hash: str) -> str:
        event = {
            "repository": repository,
            "pr_number": pr_number,
            "head_sha": head_sha.lower(),
            "observed_at": observed_at,
            "payload_sha256": payload_sha256,
            "previous_hash": previous_hash,
        }
        canonical = json.dumps(event, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @staticmethod
    def _validate_identity(repository: str, pr_number: int, head_sha: str) -> tuple[str, int, str]:
        if not isinstance(repository, str) or repository.count("/") != 1:
            raise EngineeringEvidenceError("repository must use owner/name format")
        owner, repo = repository.split("/", 1)
        if (owner in {".", ".."} or repo in {".", ".."}
                or not _REPO_PART_RE.fullmatch(owner)
                or not _REPO_PART_RE.fullmatch(repo)):
            raise EngineeringEvidenceError("repository contains invalid characters")
        if type(pr_number) is not int or pr_number < 1:
            raise EngineeringEvidenceError("PR number must be a positive integer")
        if not isinstance(head_sha, str) or not _SHA_RE.fullmatch(head_sha):
            raise EngineeringEvidenceError("head SHA must be a 40-character commit SHA")
        return repository.lower(), pr_number, head_sha.lower()

    def append(self, *, repository: str, pr_number: int, head_sha: str,
               payload: dict[str, Any]) -> int:
        repository, pr_number, head_sha = self._validate_identity(repository, pr_number, head_sha)
        payload_json = self._canonical_json(payload)
        payload_sha256 = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
        observed_at = datetime.now(timezone.utc).isoformat()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                previous = db.execute(
                    "SELECT event_hash FROM engineering_evidence ORDER BY evidence_id DESC LIMIT 1"
                ).fetchone()
                previous_hash = previous[0] if previous else _ZERO_HASH
                event_hash = self._event_hash(
                    repository, pr_number, head_sha, observed_at, payload_sha256, previous_hash
                )
                cursor = db.execute("""
                    INSERT INTO engineering_evidence
                    (repository, pr_number, head_sha, observed_at, payload_json,
                     payload_sha256, previous_hash, event_hash)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (repository, pr_number, head_sha, observed_at, payload_json,
                      payload_sha256, previous_hash, event_hash))
                db.execute("COMMIT")
                return int(cursor.lastrowid)
            except Exception:
                db.execute("ROLLBACK")
                raise

    def list_for_pull_request(self, *, repository: str, pr_number: int,
                              limit: int = 100) -> list[dict[str, Any]]:
        repository, pr_number, _ = self._validate_identity(repository, pr_number, "0" * 40)
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        with self._connect() as db:
            rows = db.execute("""
                SELECT evidence_id, repository, pr_number, head_sha, observed_at,
                       payload_json, payload_sha256, previous_hash, event_hash
                FROM engineering_evidence
                WHERE repository = ? AND pr_number = ?
                ORDER BY evidence_id DESC LIMIT ?
            """, (repository, pr_number, limit)).fetchall()
        keys = ("evidence_id", "repository", "pr_number", "head_sha", "observed_at",
                "payload_json", "payload_sha256", "previous_hash", "event_hash")
        results = []
        for row in rows:
            item = dict(zip(keys, row))
            try:
                item["payload"] = json.loads(item.pop("payload_json"))
            except json.JSONDecodeError as exc:
                raise EngineeringEvidenceError("stored evidence JSON is corrupt") from exc
            results.append(item)
        return results

    def verify_chain(self) -> bool:
        with self._connect() as db:
            rows = db.execute("""
                SELECT repository, pr_number, head_sha, observed_at, payload_json,
                       payload_sha256, previous_hash, event_hash
                FROM engineering_evidence ORDER BY evidence_id ASC
            """).fetchall()
        previous_hash = _ZERO_HASH
        for repository, pr_number, head_sha, observed_at, payload_json, payload_sha, stored_previous, stored_hash in rows:
            if not all(isinstance(value, str) for value in
                       (repository, head_sha, observed_at, payload_json, payload_sha, stored_previous, stored_hash)):
                return False
            actual_payload_hash = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
            if not hmac.compare_digest(actual_payload_hash, payload_sha):
                return False
            if not hmac.compare_digest(stored_previous, previous_hash):
                return False
            expected = self._event_hash(
                repository, pr_number, head_sha, observed_at, payload_sha, stored_previous
            )
            if not hmac.compare_digest(expected, stored_hash):
                return False
            previous_hash = stored_hash
        return True
