"""Durable host-controlled reviewer leases with fencing generations."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
from pathlib import Path
import re
import secrets
import sqlite3
from typing import Callable

_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_SHA = re.compile(r"^[a-f0-9]{64}$")


class ReviewerLeaseError(RuntimeError):
    pass


class ReviewerLeaseConflict(ReviewerLeaseError):
    pass


@dataclass(frozen=True)
class ReviewerLease:
    task_id: str
    artifact_sha256: str
    reviewer_agent_id: str
    generation: int
    expires_at: str
    token: str


class ReviewerLeaseAuthority:
    """SQLite lease registry; callers must still enforce returned generations.

    The HMAC secret is supplied by the host and is never stored in SQLite.
    Recovery is an explicit authorized transition and increments generation,
    fencing tokens issued to all previous claimants.
    """

    def __init__(self, database_path: str | Path, *, secret: bytes,
                 authorize: Callable[[str, str, str], bool]):
        if not isinstance(secret, bytes) or len(secret) < 32:
            raise ValueError("reviewer_lease_secret_must_be_32_bytes")
        if not callable(authorize):
            raise TypeError("trusted_reviewer_lease_authorizer_required")
        self._secret = secret
        self._authorize = authorize
        self.database_path = str(database_path)
        if not self.database_path.strip():
            raise ValueError("reviewer_lease_database_required")
        Path(self.database_path).expanduser().parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS reviewer_leases (
                task_id TEXT PRIMARY KEY, artifact_sha256 TEXT NOT NULL,
                reviewer_agent_id TEXT NOT NULL, generation INTEGER NOT NULL,
                expires_at TEXT NOT NULL, token_nonce TEXT NOT NULL,
                updated_at TEXT NOT NULL)""")
            db.execute("""CREATE TABLE IF NOT EXISTS reviewer_lease_events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL,
                generation INTEGER NOT NULL, event_type TEXT NOT NULL,
                actor_id TEXT NOT NULL, reviewer_agent_id TEXT NOT NULL,
                occurred_at TEXT NOT NULL, details_json TEXT NOT NULL)""")

    def _connect(self):
        db = sqlite3.connect(self.database_path, timeout=10)
        db.execute("PRAGMA foreign_keys=ON")
        return db

    @staticmethod
    def _validate(task_id, artifact_sha256, reviewer_agent_id):
        if not isinstance(task_id, str) or not _ID.fullmatch(task_id):
            raise ValueError("invalid_reviewer_lease_task_id")
        if not isinstance(artifact_sha256, str) or not _SHA.fullmatch(artifact_sha256):
            raise ValueError("invalid_reviewer_lease_artifact_digest")
        if not isinstance(reviewer_agent_id, str) or not _ID.fullmatch(reviewer_agent_id):
            raise ValueError("invalid_reviewer_lease_reviewer")

    def _allowed(self, actor, action, digest):
        try:
            return self._authorize(actor, action, digest) is True
        except Exception:
            return False

    def _issue_token(self, *, task_id, digest, reviewer, generation, expires, nonce):
        payload = {
            "v": 1, "task_id": task_id, "artifact_sha256": digest,
            "reviewer_agent_id": reviewer, "generation": generation,
            "expires_at": expires, "nonce": nonce,
        }
        body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        sig = hmac.new(self._secret, body, hashlib.sha256).hexdigest()
        return body.hex() + "." + sig

    def _decode(self, token):
        if not isinstance(token, str) or len(token) > 4096 or token.count(".") != 1:
            raise ReviewerLeaseError("invalid_reviewer_lease_token")
        body_hex, supplied = token.split(".", 1)
        if not re.fullmatch(r"[a-f0-9]+", body_hex) or not re.fullmatch(r"[a-f0-9]{64}", supplied):
            raise ReviewerLeaseError("invalid_reviewer_lease_token")
        body = bytes.fromhex(body_hex)
        expected = hmac.new(self._secret, body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, supplied):
            raise ReviewerLeaseError("reviewer_lease_signature_invalid")
        payload = json.loads(body.decode("utf-8"))
        if not isinstance(payload, dict) or json.dumps(payload, sort_keys=True, separators=(",", ":")).encode() != body:
            raise ReviewerLeaseError("noncanonical_reviewer_lease_token")
        return payload

    def acquire(self, *, actor_id: str, task_id: str, artifact_sha256: str,
                reviewer_agent_id: str, ttl_seconds: int = 300) -> ReviewerLease:
        self._validate(task_id, artifact_sha256, reviewer_agent_id)
        if not isinstance(actor_id, str) or not _ID.fullmatch(actor_id):
            raise ValueError("invalid_reviewer_lease_actor")
        if type(ttl_seconds) is not int or not 30 <= ttl_seconds <= 3600:
            raise ValueError("invalid_reviewer_lease_ttl")
        if not self._allowed(actor_id, "review:lease:acquire", artifact_sha256):
            raise PermissionError("reviewer_lease_acquisition_not_authorized")
        now = datetime.now(timezone.utc)
        expires = (now + timedelta(seconds=ttl_seconds)).isoformat()
        nonce = secrets.token_urlsafe(24)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM reviewer_leases WHERE task_id=?", (task_id,)).fetchone()
            if row and row[1] != artifact_sha256:
                raise ReviewerLeaseConflict("reviewer_lease_artifact_conflict")
            if row and datetime.fromisoformat(row[4]) > now:
                raise ReviewerLeaseConflict("reviewer_lease_already_active")
            generation = (row[3] + 1) if row else 1
            token = self._issue_token(task_id=task_id, digest=artifact_sha256,
                reviewer=reviewer_agent_id, generation=generation, expires=expires, nonce=nonce)
            db.execute("""INSERT INTO reviewer_leases VALUES (?,?,?,?,?,?,?)
                ON CONFLICT(task_id) DO UPDATE SET artifact_sha256=excluded.artifact_sha256,
                reviewer_agent_id=excluded.reviewer_agent_id,generation=excluded.generation,
                expires_at=excluded.expires_at,token_nonce=excluded.token_nonce,
                updated_at=excluded.updated_at""",
                (task_id, artifact_sha256, reviewer_agent_id, generation, expires, nonce, now.isoformat()))
            db.execute("INSERT INTO reviewer_lease_events(task_id,generation,event_type,actor_id,reviewer_agent_id,occurred_at,details_json) VALUES(?,?,?,?,?,?,?)",
                (task_id,generation,"acquired",actor_id,reviewer_agent_id,now.isoformat(),
                 json.dumps({"expires_at": expires},sort_keys=True)))
        return ReviewerLease(task_id, artifact_sha256, reviewer_agent_id, generation, expires, token)

    def validate(self, token: str, *, task_id: str, artifact_sha256: str,
                 reviewer_agent_id: str, generation: int) -> bool:
        try:
            self._validate(task_id, artifact_sha256, reviewer_agent_id)
            if type(generation) is not int or generation < 1:
                return False
            payload = self._decode(token)
            if (payload.get("task_id") != task_id or payload.get("artifact_sha256") != artifact_sha256
                    or payload.get("reviewer_agent_id") != reviewer_agent_id
                    or payload.get("generation") != generation
                    or datetime.fromisoformat(payload["expires_at"]) <= datetime.now(timezone.utc)):
                return False
            with self._connect() as db:
                row = db.execute("SELECT artifact_sha256,reviewer_agent_id,generation,expires_at,token_nonce FROM reviewer_leases WHERE task_id=?",
                                 (task_id,)).fetchone()
            return bool(row and row == (artifact_sha256, reviewer_agent_id, generation,
                                        payload["expires_at"], payload["nonce"]))
        except (ReviewerLeaseError, ValueError, KeyError, TypeError, sqlite3.Error):
            return False

    def renew(self, *, actor_id: str, token: str, ttl_seconds: int = 300) -> ReviewerLease:
        payload = self._decode(token)
        task_id, digest, reviewer = (payload.get("task_id"), payload.get("artifact_sha256"),
                                     payload.get("reviewer_agent_id"))
        self._validate(task_id, digest, reviewer)
        if type(ttl_seconds) is not int or not 30 <= ttl_seconds <= 3600:
            raise ValueError("invalid_reviewer_lease_ttl")
        if not self._allowed(actor_id, "review:lease:renew", digest):
            raise PermissionError("reviewer_lease_renewal_not_authorized")
        if not self.validate(token, task_id=task_id, artifact_sha256=digest,
                             reviewer_agent_id=reviewer, generation=payload.get("generation")):
            raise ReviewerLeaseConflict("reviewer_lease_expired_or_superseded")
        now = datetime.now(timezone.utc)
        expires = (now + timedelta(seconds=ttl_seconds)).isoformat()
        nonce = secrets.token_urlsafe(24)
        renewed = self._issue_token(task_id=task_id,digest=digest,reviewer=reviewer,
            generation=payload["generation"],expires=expires,nonce=nonce)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            changed = db.execute("""UPDATE reviewer_leases SET expires_at=?,token_nonce=?,updated_at=?
                WHERE task_id=? AND generation=? AND token_nonce=?""",
                (expires,nonce,now.isoformat(),task_id,payload["generation"],payload["nonce"])).rowcount
            if changed != 1:
                raise ReviewerLeaseConflict("reviewer_lease_changed_during_renewal")
            db.execute("INSERT INTO reviewer_lease_events(task_id,generation,event_type,actor_id,reviewer_agent_id,occurred_at,details_json) VALUES(?,?,?,?,?,?,?)",
                (task_id,payload["generation"],"renewed",actor_id,reviewer,now.isoformat(),
                 json.dumps({"expires_at":expires},sort_keys=True)))
        return ReviewerLease(task_id,digest,reviewer,payload["generation"],expires,renewed)

    def recover(self, *, actor_id: str, task_id: str, artifact_sha256: str,
                replacement_reviewer_agent_id: str, ttl_seconds: int = 300) -> ReviewerLease:
        """Explicitly supersede an expired lease; generation always increases."""
        if not isinstance(actor_id, str) or not _ID.fullmatch(actor_id):
            raise ValueError("invalid_reviewer_lease_actor")
        self._validate(task_id, artifact_sha256, replacement_reviewer_agent_id)
        if not self._allowed(actor_id, "review:lease:recover", artifact_sha256):
            raise PermissionError("reviewer_lease_recovery_not_authorized")
        return self.acquire(actor_id=actor_id,task_id=task_id,artifact_sha256=artifact_sha256,
            reviewer_agent_id=replacement_reviewer_agent_id,ttl_seconds=ttl_seconds)

    def history(self, *, actor_id: str, task_id: str, artifact_sha256: str) -> tuple[dict, ...]:
        self._validate(task_id, artifact_sha256, "history-reader")
        if not self._allowed(actor_id, "review:lease:audit", artifact_sha256):
            raise PermissionError("reviewer_lease_audit_not_authorized")
        with self._connect() as db:
            rows = db.execute("""SELECT generation,event_type,actor_id,reviewer_agent_id,occurred_at,details_json
                FROM reviewer_lease_events WHERE task_id=? ORDER BY event_id""",(task_id,)).fetchall()
        return tuple({"generation":r[0],"event_type":r[1],"actor_id":r[2],
                      "reviewer_agent_id":r[3],"occurred_at":r[4],"details":json.loads(r[5])}
                     for r in rows)


__all__ = ["ReviewerLease", "ReviewerLeaseAuthority", "ReviewerLeaseError", "ReviewerLeaseConflict"]
