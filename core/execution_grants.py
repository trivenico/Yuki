"""Durable, actor-bound HMAC execution grants.

The signing key must be supplied by a trusted secret/keyring provider at startup.
This module never generates a fallback key, writes key material to SQLite, or
stores bearer tokens in plaintext. The SQLite journal stores claims and a token
digest so revocation remains effective across process restarts.
"""
from __future__ import annotations

import base64
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import re
import secrets
import sqlite3
from typing import Any, Callable, Mapping

from core.code_execution import (
    CodeExecutionContractError,
    CodeExecutionRequest,
    ExecutionGrant,
)
from core.sqlite_file_security import prepare_private_sqlite_path

_KEY_ID = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
_MAX_TTL_SECONDS = 900


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> bytes:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise ValueError("invalid base64url token segment")
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


class HMACExecutionGrantAuthority:
    """Issue, verify, and revoke short-lived grants using an externally held key."""

    def __init__(
        self,
        database_path: str,
        *,
        key_id: str,
        signing_key: bytes,
        authorize_request: Callable[[CodeExecutionRequest], bool],
    ) -> None:
        if not isinstance(key_id, str) or not _KEY_ID.fullmatch(key_id):
            raise ValueError("key_id must be a bounded identifier")
        if not isinstance(signing_key, bytes) or len(signing_key) < 32:
            raise ValueError("signing_key must contain at least 32 bytes from a secret store")
        if not callable(authorize_request):
            raise TypeError("authorize_request policy callback is required")
        self._authorize_request = authorize_request
        self._key_id = key_id
        self._signing_key = bytes(signing_key)
        self.database_path = prepare_private_sqlite_path(database_path)
        with self._connect() as db:
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS execution_grants (
                    execution_id TEXT PRIMARY KEY,
                    actor_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    manifest_sha256 TEXT NOT NULL,
                    allowed_tools_json TEXT NOT NULL,
                    key_id TEXT NOT NULL,
                    token_sha256 TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    revoked_at TEXT,
                    revoked_by TEXT
                )
                """
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_execution_grants_actor "
                "ON execution_grants(actor_id, expires_at)"
            )
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS execution_grant_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    execution_id TEXT NOT NULL,
                    actor_id TEXT NOT NULL,
                    event_kind TEXT NOT NULL CHECK(event_kind IN ('issued', 'revoked')),
                    created_at TEXT NOT NULL
                )
                """
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_execution_grant_events_execution "
                "ON execution_grant_events(execution_id, event_id)"
            )

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.database_path, timeout=5.0)
        try:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA busy_timeout=5000")
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("PRAGMA synchronous=FULL")
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def _encode(self, payload: dict[str, Any]) -> str:
        encoded = _b64encode(
            json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
        )
        signing_input = f"v1.{self._key_id}.{encoded}".encode("ascii")
        signature = hmac.new(self._signing_key, signing_input, hashlib.sha256).digest()
        return f"v1.{self._key_id}.{encoded}.{_b64encode(signature)}"

    def _decode(self, token: str) -> dict[str, Any]:
        if not isinstance(token, str) or len(token) > 8192:
            raise CodeExecutionContractError("execution grant token is malformed")
        parts = token.split(".")
        if len(parts) != 4 or parts[0] != "v1" or parts[1] != self._key_id:
            raise CodeExecutionContractError("execution grant key or version is invalid")
        try:
            payload_bytes = _b64decode(parts[2])
            signature = _b64decode(parts[3])
            signing_input = f"{parts[0]}.{parts[1]}.{parts[2]}".encode("ascii")
            expected = hmac.new(self._signing_key, signing_input, hashlib.sha256).digest()
            if not hmac.compare_digest(signature, expected):
                raise CodeExecutionContractError("execution grant signature is invalid")
            payload = json.loads(payload_bytes.decode("utf-8"))
        except CodeExecutionContractError:
            raise
        except (ValueError, UnicodeError, json.JSONDecodeError) as exc:
            raise CodeExecutionContractError("execution grant token is malformed") from exc
        if not isinstance(payload, dict):
            raise CodeExecutionContractError("execution grant payload is invalid")
        return payload

    def issue(self, request: CodeExecutionRequest, *, ttl_seconds: int = 300) -> ExecutionGrant:
        request.validate()
        try:
            authorized = self._authorize_request(request)
        except Exception as exc:
            raise CodeExecutionContractError("execution request authorization failed") from exc
        if authorized is not True:
            raise CodeExecutionContractError("execution request is not approved for grant issuance")
        if type(ttl_seconds) is not int or not 1 <= ttl_seconds <= _MAX_TTL_SECONDS:
            raise ValueError("ttl_seconds must be between 1 and 900")
        now = datetime.now(timezone.utc)
        expires_at = (now + timedelta(seconds=ttl_seconds)).isoformat()
        nonce = secrets.token_urlsafe(16)
        payload = {
            "actor_id": request.actor_id,
            "session_id": request.session_id,
            "task_id": request.task_id,
            "execution_id": request.execution_id,
            "manifest_sha256": request.manifest_sha256,
            "allowed_tools": list(request.tool_names),
            "expires_at": expires_at,
            "nonce": nonce,
        }
        token = self._encode(payload)
        token_digest = hashlib.sha256(token.encode("ascii")).hexdigest()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute(
                "SELECT 1 FROM execution_grants WHERE execution_id=?",
                (request.execution_id,),
            ).fetchone()
            if existing is not None:
                raise CodeExecutionContractError("execution grant already exists for this execution ID")
            db.execute(
                """
                INSERT INTO execution_grants (
                    execution_id, actor_id, session_id, task_id, manifest_sha256,
                    allowed_tools_json, key_id, token_sha256, expires_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    request.execution_id, request.actor_id, request.session_id,
                    request.task_id, request.manifest_sha256,
                    json.dumps(list(request.tool_names), separators=(",", ":")),
                    self._key_id, token_digest, expires_at, now.isoformat(),
                ),
            )
            db.execute(
                "INSERT INTO execution_grant_events "
                "(execution_id, actor_id, event_kind, created_at) VALUES (?, ?, 'issued', ?)",
                (request.execution_id, request.actor_id, now.isoformat()),
            )
        return ExecutionGrant(
            actor_id=request.actor_id,
            session_id=request.session_id,
            task_id=request.task_id,
            execution_id=request.execution_id,
            manifest_sha256=request.manifest_sha256,
            allowed_tools=request.tool_names,
            expires_at=expires_at,
            token=token,
        )

    def _verify_in_transaction(
        self,
        db: sqlite3.Connection,
        grant: ExecutionGrant,
        request: CodeExecutionRequest,
    ) -> bool:
        try:
            grant.validate_for(request)
            payload = self._decode(grant.token)
        except (CodeExecutionContractError, TypeError, ValueError):
            return False
        expected_payload = {
            "actor_id": request.actor_id,
            "session_id": request.session_id,
            "task_id": request.task_id,
            "execution_id": request.execution_id,
            "manifest_sha256": request.manifest_sha256,
            "allowed_tools": list(request.tool_names),
            "expires_at": grant.expires_at,
        }
        if any(payload.get(key) != value for key, value in expected_payload.items()):
            return False
        if not isinstance(payload.get("nonce"), str) or not payload["nonce"]:
            return False
        try:
            token_digest = hashlib.sha256(grant.token.encode("ascii")).hexdigest()
            row = db.execute(
                "SELECT * FROM execution_grants WHERE execution_id=?",
                (request.execution_id,),
            ).fetchone()
        except (sqlite3.Error, UnicodeError, TypeError, ValueError):
            return False
        if row is None or row["revoked_at"] is not None:
            return False
        try:
            unexpired = datetime.fromisoformat(row["expires_at"]) > datetime.now(timezone.utc)
            return (
                row["actor_id"] == request.actor_id
                and row["session_id"] == request.session_id
                and row["task_id"] == request.task_id
                and row["manifest_sha256"] == request.manifest_sha256
                and row["allowed_tools_json"] == json.dumps(list(request.tool_names), separators=(",", ":"))
                and row["key_id"] == self._key_id
                and isinstance(row["token_sha256"], str)
                and hmac.compare_digest(row["token_sha256"], token_digest)
                and row["expires_at"] == grant.expires_at
                and unexpired
            )
        except (TypeError, ValueError, KeyError):
            return False

    def verify(self, grant: ExecutionGrant, request: CodeExecutionRequest) -> bool:
        try:
            with self._connect() as db:
                return self._verify_in_transaction(db, grant, request)
        except sqlite3.Error:
            return False

    def authorize_tool_call(
        self,
        grant: ExecutionGrant,
        request: CodeExecutionRequest,
        *,
        tool_name: str,
        operation: Callable[[], Mapping[str, object]],
    ) -> Mapping[str, object]:
        """Hold the SQLite write reservation across authorization and RPC.

        Revocation takes the same write lock, so it is ordered either before
        this tool call (which then fails authorization) or after it completes.
        Host operations must remain bounded because they briefly serialize grant
        issuance/revocation in this local grant journal.
        """
        if not isinstance(tool_name, str) or tool_name not in request.tool_names:
            raise CodeExecutionContractError("tool is not authorized by the execution request")
        if not callable(operation):
            raise TypeError("operation must be callable")
        try:
            with self._connect() as db:
                db.execute("BEGIN IMMEDIATE")
                if not self._verify_in_transaction(db, grant, request):
                    raise CodeExecutionContractError("execution grant is not trusted for tool dispatch")
                return operation()
        except CodeExecutionContractError:
            raise
        except sqlite3.Error as exc:
            raise CodeExecutionContractError("execution grant journal is unavailable") from exc

    def revoke(self, execution_id: str, *, actor_id: str) -> bool:
        if not isinstance(execution_id, str) or not execution_id.strip():
            raise ValueError("execution_id is required")
        if (
            not isinstance(actor_id, str) or not actor_id.strip()
            or len(actor_id) > 200 or "\x00" in actor_id
        ):
            raise ValueError("actor_id is required and must be bounded text")
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            cursor = db.execute(
                """
                UPDATE execution_grants
                SET revoked_at=?, revoked_by=?
                WHERE execution_id=? AND actor_id=? AND revoked_at IS NULL
                """,
                (now, actor_id, execution_id, actor_id),
            )
            if cursor.rowcount == 1:
                db.execute(
                    "INSERT INTO execution_grant_events "
                    "(execution_id, actor_id, event_kind, created_at) "
                    "VALUES (?, ?, 'revoked', ?)",
                    (execution_id, actor_id, now),
                )
                return True
            return False
