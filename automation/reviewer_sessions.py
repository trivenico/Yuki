"""Host-issued, artifact-scoped reviewer sessions with replay protection.

Tokens are bearer credentials: deliver them only through the trusted worker
transport, never through prompts or shared task descriptions. The host secret
must come from protected local storage and is never persisted by this module.
"""
from __future__ import annotations

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
_ACTIONS = frozenset({"review:execute", "review:submit"})


class ReviewerSessionError(RuntimeError):
    pass


class ReviewerSessionAuthority:
    def __init__(self, database_path: str | Path, *, secret: bytes,
                 authorize: Callable[[str, str, str], bool],
                 session_ttl_seconds: int = 900):
        if not isinstance(secret, bytes) or len(secret) < 32:
            raise ValueError("reviewer_session_secret_must_be_at_least_32_bytes")
        if not callable(authorize):
            raise TypeError("trusted_reviewer_session_authorizer_required")
        if type(session_ttl_seconds) is not int or not 30 <= session_ttl_seconds <= 3600:
            raise ValueError("reviewer_session_ttl_must_be_30_to_3600_seconds")
        self.database_path = str(database_path)
        if not self.database_path.strip():
            raise ValueError("database_path_required")
        Path(self.database_path).expanduser().parent.mkdir(parents=True, exist_ok=True)
        self._secret = secret
        self._authorize = authorize
        self._ttl = session_ttl_seconds
        with self._connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=FULL")
            db.execute("""CREATE TABLE IF NOT EXISTS reviewer_sessions (
                session_id TEXT PRIMARY KEY,
                reviewer_agent_id TEXT NOT NULL,
                artifact_sha256 TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                revoked INTEGER NOT NULL DEFAULT 0
            )""")
            db.execute("""CREATE TABLE IF NOT EXISTS reviewer_session_uses (
                session_id TEXT NOT NULL,
                operation_id TEXT NOT NULL,
                action TEXT NOT NULL,
                used_at TEXT NOT NULL,
                PRIMARY KEY(session_id, operation_id, action),
                FOREIGN KEY(session_id) REFERENCES reviewer_sessions(session_id)
            )""")

    def _connect(self):
        db = sqlite3.connect(self.database_path, timeout=10)
        db.execute("PRAGMA foreign_keys=ON")
        return db

    @staticmethod
    def _canonical(payload: dict) -> bytes:
        return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")

    def _token(self, payload: dict) -> str:
        body = self._canonical(payload)
        signature = hmac.new(self._secret, body, hashlib.sha256).hexdigest()
        return body.hex() + "." + signature

    def _decode(self, token: str) -> dict:
        if not isinstance(token, str) or len(token) > 4096 or token.count(".") != 1:
            raise ReviewerSessionError("invalid_reviewer_session_token")
        body_hex, signature = token.split(".", 1)
        if not re.fullmatch(r"[a-f0-9]+", body_hex) or not re.fullmatch(r"[a-f0-9]{64}", signature):
            raise ReviewerSessionError("invalid_reviewer_session_token")
        try:
            body = bytes.fromhex(body_hex)
            expected = hmac.new(self._secret, body, hashlib.sha256).hexdigest()
            if not hmac.compare_digest(expected, signature):
                raise ReviewerSessionError("reviewer_session_signature_invalid")
            payload = json.loads(body.decode("utf-8"))
        except (ValueError, UnicodeError, json.JSONDecodeError) as exc:
            raise ReviewerSessionError("invalid_reviewer_session_token") from exc
        if not isinstance(payload, dict) or self._canonical(payload) != body:
            raise ReviewerSessionError("noncanonical_reviewer_session_token")
        return payload

    def issue(self, *, reviewer_agent_id: str, artifact_sha256: str,
              actions=("review:execute", "review:submit")) -> str:
        if not isinstance(reviewer_agent_id, str) or not _ID.fullmatch(reviewer_agent_id):
            raise ValueError("invalid_reviewer_identity")
        if not isinstance(artifact_sha256, str) or not _SHA.fullmatch(artifact_sha256):
            raise ValueError("invalid_artifact_sha256")
        if (not isinstance(actions, (tuple, list, set, frozenset))
                or any(not isinstance(action, str) for action in actions)):
            raise ValueError("invalid_reviewer_session_actions")
        granted = tuple(sorted(set(actions)))
        if not granted or any(action not in _ACTIONS for action in granted):
            raise ValueError("invalid_reviewer_session_actions")
        for action in granted:
            try:
                allowed = self._authorize(reviewer_agent_id, action, artifact_sha256) is True
            except Exception:
                allowed = False
            if not allowed:
                raise PermissionError("reviewer_session_action_not_authorized")
        now = datetime.now(timezone.utc)
        payload = {
            "version": 1,
            "session_id": secrets.token_urlsafe(24),
            "reviewer_agent_id": reviewer_agent_id,
            "artifact_sha256": artifact_sha256,
            "actions": list(granted),
            "issued_at": now.isoformat(),
            "expires_at": (now + timedelta(seconds=self._ttl)).isoformat(),
        }
        with self._connect() as db:
            db.execute("INSERT INTO reviewer_sessions VALUES (?,?,?,?,0)", (
                payload["session_id"], reviewer_agent_id, artifact_sha256, payload["expires_at"]
            ))
        return self._token(payload)

    def validate(self, token: str, *, reviewer_agent_id: str,
                 artifact_sha256: str, action: str, operation_id: str) -> bool:
        if (not isinstance(reviewer_agent_id, str) or not _ID.fullmatch(reviewer_agent_id)
                or not isinstance(artifact_sha256, str) or not _SHA.fullmatch(artifact_sha256)
                or not isinstance(action, str) or action not in _ACTIONS
                or not isinstance(operation_id, str) or not _ID.fullmatch(operation_id)):
            return False
        try:
            payload = self._decode(token)
            if (payload.get("version") != 1
                    or payload.get("reviewer_agent_id") != reviewer_agent_id
                    or payload.get("artifact_sha256") != artifact_sha256
                    or action not in payload.get("actions", [])
                    or datetime.fromisoformat(payload["expires_at"]) <= datetime.now(timezone.utc)):
                return False
            if self._authorize(reviewer_agent_id, action, artifact_sha256) is not True:
                return False
            with self._connect() as db:
                db.execute("BEGIN IMMEDIATE")
                row = db.execute(
                    "SELECT reviewer_agent_id,artifact_sha256,revoked,expires_at FROM reviewer_sessions WHERE session_id=?",
                    (payload.get("session_id"),)
                ).fetchone()
                if (row is None or row[0] != reviewer_agent_id
                        or row[1] != artifact_sha256 or row[2] != 0
                        or row[3] != payload["expires_at"]):
                    return False
                db.execute(
                    "INSERT INTO reviewer_session_uses VALUES (?,?,?,?)",
                    (payload["session_id"], operation_id, action, datetime.now(timezone.utc).isoformat())
                )
            return True
        except (ReviewerSessionError, PermissionError, ValueError, KeyError, TypeError, sqlite3.IntegrityError):
            return False

    def revoke(self, *, session_id: str) -> None:
        if not isinstance(session_id, str) or not session_id.strip():
            raise ValueError("session_id_required")
        with self._connect() as db:
            changed = db.execute(
                "UPDATE reviewer_sessions SET revoked=1 WHERE session_id=?", (session_id,)
            ).rowcount
            if changed != 1:
                raise KeyError("reviewer_session_not_found")


__all__ = ["ReviewerSessionAuthority", "ReviewerSessionError"]
