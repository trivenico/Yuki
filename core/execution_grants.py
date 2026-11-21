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
    CodeExecutionManifest,
    CodeExecutionRequest,
    ExecutionGrant,
)
from core.sqlite_file_security import prepare_private_sqlite_path

_KEY_ID = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
_MAX_TTL_SECONDS = 900
_MAX_ACTIVE_REMOTE_EXECUTIONS_PER_TASK = 1
_MAX_ACTIVE_REMOTE_EXECUTIONS_PER_ACTOR = 3
_REMOTE_EXECUTION_TERMINATION_GRACE_SECONDS = 30


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
        authorize_request: Callable[[CodeExecutionRequest | CodeExecutionManifest], bool],
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
            grant_columns = {row["name"] for row in db.execute("PRAGMA table_info(execution_grants)")}
            if "consumed_at" not in grant_columns:
                db.execute("ALTER TABLE execution_grants ADD COLUMN consumed_at TEXT")
            if "execution_deadline" not in grant_columns:
                db.execute("ALTER TABLE execution_grants ADD COLUMN execution_deadline TEXT")
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
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS execution_tool_calls (
                    execution_id TEXT NOT NULL,
                    call_index INTEGER NOT NULL CHECK(call_index >= 0),
                    tool_name TEXT NOT NULL,
                    request_sha256 TEXT NOT NULL,
                    state TEXT NOT NULL CHECK(state IN ('pending', 'completed', 'failed')),
                    result_json TEXT,
                    created_at TEXT NOT NULL,
                    completed_at TEXT,
                    PRIMARY KEY(execution_id, call_index),
                    FOREIGN KEY(execution_id) REFERENCES execution_grants(execution_id) ON DELETE CASCADE
                )
                """
            )
            db.execute(
                "CREATE INDEX IF NOT EXISTS idx_execution_tool_calls_state "
                "ON execution_tool_calls(execution_id, state)"
            )
            db.execute(
                """
                CREATE TABLE IF NOT EXISTS execution_completions (
                    execution_id TEXT PRIMARY KEY,
                    manifest_sha256 TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('succeeded', 'failed', 'cancelled')),
                    duration_seconds REAL,
                    tool_calls INTEGER NOT NULL CHECK(tool_calls >= 0),
                    reported_tool_calls INTEGER NOT NULL DEFAULT 0 CHECK(reported_tool_calls >= 0),
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(execution_id) REFERENCES execution_grants(execution_id) ON DELETE CASCADE
                )
                """
            )
            completion_columns = {
                row["name"] for row in db.execute("PRAGMA table_info(execution_completions)")
            }
            if "reported_tool_calls" not in completion_columns:
                db.execute(
                    "ALTER TABLE execution_completions ADD COLUMN "
                    "reported_tool_calls INTEGER NOT NULL DEFAULT 0"
                )

    @property
    def supports_atomic_task_authorization(self) -> bool:
        """Whether the live policy serializes dispatch with task lease changes."""
        return callable(getattr(self._authorize_request, "authorize_operation", None))

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

    def resolve_grant(
        self,
        token: str,
        request: CodeExecutionRequest | CodeExecutionManifest,
        *,
        allow_expired: bool = False,
    ) -> ExecutionGrant:
        """Reconstruct a signed grant from an authenticated remote RPC token."""
        request.validate()
        payload = self._decode(token)
        allowed_tools = payload.get("allowed_tools")
        expires_at = payload.get("expires_at")
        if (
            not isinstance(allowed_tools, list)
            or any(not isinstance(name, str) for name in allowed_tools)
            or not isinstance(expires_at, str)
            or payload.get("nonce") is None
        ):
            raise CodeExecutionContractError("execution grant payload is incomplete")
        grant = ExecutionGrant(
            actor_id=request.actor_id,
            session_id=request.session_id,
            task_id=request.task_id,
            execution_id=request.execution_id,
            manifest_sha256=request.manifest_sha256,
            allowed_tools=tuple(allowed_tools),
            expires_at=expires_at,
            token=token,
        )
        grant.validate_for(request, require_unexpired=not allow_expired)
        if (
            payload.get("actor_id") != request.actor_id
            or payload.get("session_id") != request.session_id
            or payload.get("task_id") != request.task_id
            or payload.get("execution_id") != request.execution_id
            or payload.get("manifest_sha256") != request.manifest_sha256
        ):
            raise CodeExecutionContractError("execution grant does not match remote manifest")
        return grant

    def issue(self, request: CodeExecutionRequest | CodeExecutionManifest, *, ttl_seconds: int = 300) -> ExecutionGrant:
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
        request: CodeExecutionRequest | CodeExecutionManifest,
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

    def consume_execution(
        self,
        grant: ExecutionGrant,
        request: CodeExecutionRequest | CodeExecutionManifest,
    ) -> bool:
        """Atomically consume a valid grant before a remote sandbox is started.

        Consumption is one-shot across processes and survives restarts. The
        admission slot remains reserved through the sandbox timeout plus a
        termination grace period, even if the grant is revoked or expires.
        Only terminal reconciliation or that hard execution deadline releases
        the slot; grant expiry alone does not prove the Worker has stopped.
        """
        request.validate()
        try:
            with self._connect() as db:
                db.execute("BEGIN IMMEDIATE")
                if not self._verify_in_transaction(db, grant, request):
                    return False
                def consume_under_task_lock() -> bool:
                    row = db.execute(
                        "SELECT consumed_at FROM execution_grants WHERE execution_id=?",
                        (request.execution_id,),
                    ).fetchone()
                    if row is None or row["consumed_at"] is not None:
                        return False
                    now = datetime.now(timezone.utc)
                    now_iso = now.isoformat()
                    execution_deadline = (
                        now + timedelta(
                            seconds=request.timeout_seconds + _REMOTE_EXECUTION_TERMINATION_GRACE_SECONDS
                        )
                    ).isoformat()
                    active_for_task = db.execute(
                        """
                        SELECT COUNT(*) AS total
                        FROM execution_grants AS grants
                        LEFT JOIN execution_completions AS completions
                          ON completions.execution_id = grants.execution_id
                        WHERE grants.actor_id=? AND grants.task_id=?
                          AND grants.consumed_at IS NOT NULL
                          AND (grants.execution_deadline IS NULL OR grants.execution_deadline>?)
                          AND completions.execution_id IS NULL
                        """,
                        (request.actor_id, request.task_id, now_iso),
                    ).fetchone()["total"]
                    if active_for_task >= _MAX_ACTIVE_REMOTE_EXECUTIONS_PER_TASK:
                        return False
                    active_for_actor = db.execute(
                        """
                        SELECT COUNT(*) AS total
                        FROM execution_grants AS grants
                        LEFT JOIN execution_completions AS completions
                          ON completions.execution_id = grants.execution_id
                        WHERE grants.actor_id=?
                          AND grants.consumed_at IS NOT NULL
                          AND (grants.execution_deadline IS NULL OR grants.execution_deadline>?)
                          AND completions.execution_id IS NULL
                        """,
                        (request.actor_id, now_iso),
                    ).fetchone()["total"]
                    if active_for_actor >= _MAX_ACTIVE_REMOTE_EXECUTIONS_PER_ACTOR:
                        return False
                    cursor = db.execute(
                        """
                        UPDATE execution_grants SET consumed_at=?, execution_deadline=?
                        WHERE execution_id=? AND consumed_at IS NULL AND revoked_at IS NULL
                        """,
                        (now_iso, execution_deadline, request.execution_id),
                    )
                    return cursor.rowcount == 1

                # Grant consumption is the remote sandbox startup boundary.
                # Revalidate the live task/session/approval/lease policy while
                # the task store holds its authorization transaction, so a
                # cancellation or lease replacement after grant issuance wins
                # before any untrusted computation can start.
                policy_operation = getattr(self._authorize_request, "authorize_operation", None)
                if callable(policy_operation):
                    try:
                        return policy_operation(request, consume_under_task_lock) is True
                    except CodeExecutionContractError:
                        raise
                    except Exception as exc:
                        raise CodeExecutionContractError(
                            "live Yuki task authorization failed before remote execution"
                        ) from exc
                try:
                    authorized = self._authorize_request(request)
                except Exception as exc:
                    raise CodeExecutionContractError(
                        "live Yuki policy revalidation failed before remote execution"
                    ) from exc
                if authorized is not True:
                    return False
                return consume_under_task_lock()
        except sqlite3.Error as exc:
            raise CodeExecutionContractError("execution grant journal is unavailable") from exc

    def authorize_remote_tool_call(
        self,
        grant: ExecutionGrant,
        request: CodeExecutionRequest | CodeExecutionManifest,
        *,
        call_index: int,
        tool_name: str,
        arguments: Mapping[str, object],
        operation: Callable[[], Mapping[str, object]],
    ) -> Mapping[str, object]:
        """Dispatch one remote tool call with durable at-most-once indexing.

        The pending row is committed before the host operation. A process crash
        therefore cannot silently replay a call: retries see the durable pending
        record and fail closed. Current Yuki policy is checked again immediately
        before dispatch. This API is intended for bounded read-only tools; any
        future mutating tool must additionally make its own side effect
        transactional/idempotent with this call index.
        """
        request.validate()
        if type(call_index) is not int or not 0 <= call_index < request.max_tool_calls:
            raise CodeExecutionContractError("remote tool call index is outside the approved budget")
        if not isinstance(tool_name, str) or tool_name not in request.tool_names:
            raise CodeExecutionContractError("tool is not authorized by the execution request")
        if not isinstance(arguments, Mapping) or not callable(operation):
            raise CodeExecutionContractError("remote tool call arguments or operation are invalid")
        try:
            arguments_json = json.dumps(
                dict(arguments), sort_keys=True, separators=(",", ":"),
                ensure_ascii=False, allow_nan=False,
            )
            arguments_bytes = arguments_json.encode("utf-8")
        except (TypeError, ValueError, UnicodeError) as exc:
            raise CodeExecutionContractError("remote tool arguments are not bounded JSON data") from exc
        if len(arguments_bytes) > 32_768:
            raise CodeExecutionContractError("remote tool arguments exceed the host RPC limit")
        request_digest = hashlib.sha256(
            (tool_name + "\\n" + arguments_json).encode("utf-8")
        ).hexdigest()

        # Reserve the call index durably before any host-side operation. A
        # duplicate completed request can return its cached read result only
        # while the signed grant and live Yuki request policy remain valid.
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if not self._verify_in_transaction(db, grant, request):
                raise CodeExecutionContractError("execution grant is not trusted for remote tool dispatch")
            grant_row = db.execute(
                "SELECT consumed_at FROM execution_grants WHERE execution_id=?",
                (request.execution_id,),
            ).fetchone()
            if grant_row is None or grant_row["consumed_at"] is None:
                raise CodeExecutionContractError("execution grant was not consumed before remote dispatch")
            terminal = db.execute(
                "SELECT status FROM execution_completions WHERE execution_id=?",
                (request.execution_id,),
            ).fetchone()
            if terminal is not None:
                raise CodeExecutionContractError("remote execution is already terminal")
            existing = db.execute(
                "SELECT * FROM execution_tool_calls WHERE execution_id=? AND call_index=?",
                (request.execution_id, call_index),
            ).fetchone()
            if existing is not None:
                if existing["tool_name"] != tool_name or existing["request_sha256"] != request_digest:
                    raise CodeExecutionContractError("remote tool call index was replayed with different input")
                if existing["state"] != "completed" or not isinstance(existing["result_json"], str):
                    raise CodeExecutionContractError("remote tool call is pending or previously failed")
                try:
                    cached = json.loads(existing["result_json"])
                except (TypeError, ValueError, json.JSONDecodeError) as exc:
                    raise CodeExecutionContractError("cached remote tool result is corrupt") from exc
                if not isinstance(cached, dict):
                    raise CodeExecutionContractError("cached remote tool result is invalid")
                policy_operation = getattr(self._authorize_request, "authorize_operation", None)
                if callable(policy_operation):
                    try:
                        return policy_operation(request, lambda: cached)
                    except CodeExecutionContractError:
                        raise
                    except Exception as exc:
                        raise CodeExecutionContractError("live Yuki task policy revalidation failed") from exc
                try:
                    authorized = self._authorize_request(request)
                except Exception as exc:
                    raise CodeExecutionContractError("live Yuki policy revalidation failed") from exc
                if authorized is not True:
                    raise CodeExecutionContractError("live Yuki policy no longer authorizes this execution")
                return cached
            count = db.execute(
                "SELECT COUNT(*) AS total FROM execution_tool_calls WHERE execution_id=?",
                (request.execution_id,),
            ).fetchone()["total"]
            if count >= request.max_tool_calls:
                raise CodeExecutionContractError("execution exceeded its durable tool-call budget")
            db.execute(
                """
                INSERT INTO execution_tool_calls (
                    execution_id, call_index, tool_name, request_sha256,
                    state, created_at
                ) VALUES (?, ?, ?, ?, 'pending', ?)
                """,
                (
                    request.execution_id, call_index, tool_name, request_digest,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )

        failure: Exception | None = None
        result_value: Mapping[str, object] | None = None
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if not self._verify_in_transaction(db, grant, request):
                failure = CodeExecutionContractError("execution grant is no longer trusted for remote dispatch")
            else:
                # Completion may race with the durable reservation above. Recheck
                # terminal state under the same write lock held across dispatch.
                terminal = db.execute(
                    "SELECT status FROM execution_completions WHERE execution_id=?",
                    (request.execution_id,),
                ).fetchone()
                if terminal is not None:
                    failure = CodeExecutionContractError("remote execution is already terminal")
                else:
                    row = db.execute(
                        "SELECT state, request_sha256 FROM execution_tool_calls "
                        "WHERE execution_id=? AND call_index=?",
                        (request.execution_id, call_index),
                    ).fetchone()
                    if row is None or row["state"] != "pending" or row["request_sha256"] != request_digest:
                        failure = CodeExecutionContractError("remote tool call reservation is unavailable")
                    else:
                        try:
                            policy_operation = getattr(self._authorize_request, "authorize_operation", None)
                            if callable(policy_operation):
                                value = policy_operation(request, operation)
                            else:
                                authorized = self._authorize_request(request)
                                if authorized is not True:
                                    raise CodeExecutionContractError(
                                        "live Yuki policy no longer authorizes this execution"
                                    )
                                value = operation()
                            if not isinstance(value, Mapping):
                                raise CodeExecutionContractError("host tool returned a non-mapping result")
                            result_json = json.dumps(
                                dict(value), sort_keys=True, separators=(",", ":"),
                                ensure_ascii=False, allow_nan=False,
                            )
                            if len(result_json.encode("utf-8")) > 65_536:
                                raise CodeExecutionContractError("host tool result exceeds the RPC limit")
                            result_value = dict(value)
                            db.execute(
                                """
                                UPDATE execution_tool_calls
                                SET state='completed', result_json=?, completed_at=?
                                WHERE execution_id=? AND call_index=? AND state='pending'
                                """,
                                (
                                    result_json, datetime.now(timezone.utc).isoformat(),
                                    request.execution_id, call_index,
                                ),
                            )
                        except Exception as exc:
                            failure = exc
                            db.execute(
                                """
                                UPDATE execution_tool_calls
                                SET state='failed', completed_at=?
                                WHERE execution_id=? AND call_index=? AND state='pending'
                                """,
                                (
                                    datetime.now(timezone.utc).isoformat(),
                                    request.execution_id, call_index,
                                ),
                            )
        if failure is not None:
            if isinstance(failure, CodeExecutionContractError):
                raise failure
            raise CodeExecutionContractError("remote host tool dispatch failed") from failure
        if result_value is None:
            raise CodeExecutionContractError("remote host tool dispatch produced no result")
        return result_value

    def record_remote_completion(
        self,
        grant: ExecutionGrant,
        request: CodeExecutionRequest | CodeExecutionManifest,
        *,
        status: str,
        tool_calls: int,
        duration_seconds: float | None = None,
    ) -> bool:
        """Persist one terminal Worker result after reconciling native call records."""
        request.validate()
        if status not in {"succeeded", "failed", "cancelled"}:
            raise CodeExecutionContractError("remote execution terminal status is invalid")
        if type(tool_calls) is not int or not 0 <= tool_calls <= request.max_tool_calls:
            raise CodeExecutionContractError("remote execution tool-call count is invalid")
        if duration_seconds is not None and (
            isinstance(duration_seconds, bool)
            or not isinstance(duration_seconds, (int, float))
            or duration_seconds < 0
            or duration_seconds > request.timeout_seconds + 5
        ):
            raise CodeExecutionContractError("remote execution duration is invalid")
        try:
            with self._connect() as db:
                db.execute("BEGIN IMMEDIATE")
                grant.validate_for(request, require_unexpired=False)
                payload = self._decode(grant.token)
                token_digest = hashlib.sha256(grant.token.encode("ascii")).hexdigest()
                row = db.execute(
                    "SELECT * FROM execution_grants WHERE execution_id=?",
                    (request.execution_id,),
                ).fetchone()
                if row is None or row["consumed_at"] is None:
                    raise CodeExecutionContractError("remote execution was not consumed")
                if (
                    row["actor_id"] != request.actor_id
                    or row["session_id"] != request.session_id
                    or row["task_id"] != request.task_id
                    or row["manifest_sha256"] != request.manifest_sha256
                    or row["token_sha256"] != token_digest
                    or payload.get("execution_id") != request.execution_id
                    or payload.get("actor_id") != request.actor_id
                    or payload.get("session_id") != request.session_id
                    or payload.get("task_id") != request.task_id
                    or payload.get("manifest_sha256") != request.manifest_sha256
                ):
                    raise CodeExecutionContractError("remote completion grant binding is invalid")
                calls = db.execute(
                    "SELECT state FROM execution_tool_calls WHERE execution_id=?",
                    (request.execution_id,),
                ).fetchall()
                native_tool_calls = len(calls)
                if status == "succeeded" and tool_calls != native_tool_calls:
                    raise CodeExecutionContractError("successful completion call count does not match native records")
                if status != "succeeded" and tool_calls < native_tool_calls:
                    raise CodeExecutionContractError("remote completion under-reports native tool calls")
                if status == "succeeded" and any(call["state"] != "completed" for call in calls):
                    raise CodeExecutionContractError("successful completion has unresolved native tool calls")
                existing = db.execute(
                    "SELECT * FROM execution_completions WHERE execution_id=?",
                    (request.execution_id,),
                ).fetchone()
                normalized_duration = float(duration_seconds) if duration_seconds is not None else None
                if existing is not None:
                    if (
                        existing["manifest_sha256"] == request.manifest_sha256
                        and existing["status"] == status
                        and existing["tool_calls"] == native_tool_calls
                        and existing["reported_tool_calls"] == tool_calls
                    ):
                        return True
                    raise CodeExecutionContractError("remote execution completion conflicts with prior terminal state")
                db.execute(
                    """
                    INSERT INTO execution_completions (
                        execution_id, manifest_sha256, status, duration_seconds,
                        tool_calls, reported_tool_calls, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        request.execution_id, request.manifest_sha256, status,
                        normalized_duration, native_tool_calls, tool_calls,
                        datetime.now(timezone.utc).isoformat(),
                    ),
                )
                return True
        except CodeExecutionContractError:
            raise
        except sqlite3.Error as exc:
            raise CodeExecutionContractError("execution grant journal is unavailable") from exc

    def authorize_tool_call(
        self,
        grant: ExecutionGrant,
        request: CodeExecutionRequest | CodeExecutionManifest,
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
                policy_operation = getattr(self._authorize_request, "authorize_operation", None)
                if callable(policy_operation):
                    try:
                        return policy_operation(request, operation)
                    except CodeExecutionContractError:
                        raise
                    except Exception as exc:
                        raise CodeExecutionContractError(
                            "atomic Yuki task authorization or host operation failed"
                        ) from exc
                try:
                    policy_authorized = self._authorize_request(request)
                except Exception as exc:
                    raise CodeExecutionContractError("live Yuki policy revalidation failed") from exc
                if policy_authorized is not True:
                    raise CodeExecutionContractError("live Yuki policy no longer authorizes this execution")
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
