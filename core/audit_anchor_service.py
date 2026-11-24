"""Independent SQLite-backed authorization audit checkpoint service.

The host must authenticate request identities before they reach this service.
Publisher enrollment/revocation is a separate administrative operation.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import hmac
import json
import math
import sqlite3
from typing import Callable

from core.authorization_audit import AuthorizationAuditCheckpoint
from core.sqlite_file_security import prepare_private_sqlite_path


class AuditAnchorConflict(RuntimeError):
    """The checkpoint compare-and-set or monotonicity condition failed."""


class AuditAnchorUnauthorized(PermissionError):
    """The authenticated identity is not an active publisher."""


@dataclass(frozen=True)
class StoredAuditCheckpoint:
    checkpoint: AuthorizationAuditCheckpoint
    etag: str


class SQLiteAuditAnchorService:
    """Durable anchor state with atomic monotonic compare-and-set.

    This service must run in a trust domain separate from Yuki's audit database.
    It deliberately accepts authenticated identity strings, never identities
    asserted in JSON request bodies.
    """

    def __init__(
        self,
        database_path: str,
        *,
        authorize_administrator: Callable[[str], bool],
        timeout_seconds: float = 5.0,
    ):
        if not callable(authorize_administrator):
            raise TypeError("administrator_authorizer_required")
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(timeout_seconds)
            or timeout_seconds <= 0
            or timeout_seconds > 30
        ):
            raise ValueError("anchor_database_timeout_invalid")
        self.database_path = prepare_private_sqlite_path(database_path)
        self.authorize_administrator = authorize_administrator
        self.timeout_seconds = float(timeout_seconds)
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS anchor_checkpoint (
                    singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
                    event_id INTEGER NOT NULL CHECK(event_id >= 0),
                    event_hash TEXT NOT NULL,
                    published_at TEXT NOT NULL,
                    revision INTEGER NOT NULL CHECK(revision >= 1)
                );
                CREATE TABLE IF NOT EXISTS anchor_publishers (
                    identity TEXT PRIMARY KEY,
                    active INTEGER NOT NULL CHECK(active IN (0, 1)),
                    updated_at TEXT NOT NULL,
                    updated_by TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS anchor_admin_audit (
                    audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    administrator TEXT NOT NULL,
                    publisher_identity TEXT NOT NULL,
                    operation TEXT NOT NULL CHECK(operation IN ('enable', 'revoke')),
                    occurred_at TEXT NOT NULL
                );
                """
            )

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(
            self.database_path, timeout=self.timeout_seconds, isolation_level=None
        )
        db.row_factory = sqlite3.Row
        try:
            db.execute(f"PRAGMA busy_timeout = {int(self.timeout_seconds * 1000)}")
            yield db
        finally:
            db.close()

    @staticmethod
    def _identity(value: object, label: str) -> str:
        if (
            not isinstance(value, str)
            or not value
            or len(value) > 256
            or value != value.strip()
            or any(ord(char) < 33 or ord(char) > 126 for char in value)
        ):
            raise ValueError(label + "_invalid")
        return value

    @staticmethod
    def _validate_checkpoint(checkpoint: AuthorizationAuditCheckpoint) -> None:
        if (
            not isinstance(checkpoint, AuthorizationAuditCheckpoint)
            or type(checkpoint.event_id) is not int
            or checkpoint.event_id < 0
            or not isinstance(checkpoint.event_hash, str)
            or len(checkpoint.event_hash) != 64
            or any(char not in "0123456789abcdef" for char in checkpoint.event_hash)
        ):
            raise ValueError("authorization_audit_checkpoint_invalid")

    @staticmethod
    def _etag(event_id: int, event_hash: str, revision: int) -> str:
        material = f"{revision}:{event_id}:{event_hash}".encode("ascii")
        return '"' + hashlib.sha256(material).hexdigest() + '"'

    def _require_publisher(self, db: sqlite3.Connection, identity: str) -> None:
        row = db.execute(
            "SELECT active FROM anchor_publishers WHERE identity = ?", (identity,)
        ).fetchone()
        if row is None or row["active"] != 1:
            raise AuditAnchorUnauthorized("audit_anchor_publisher_not_authorized")

    def get_checkpoint(self, publisher_identity: str) -> StoredAuditCheckpoint | None:
        identity = self._identity(publisher_identity, "publisher_identity")
        with self._connect() as db:
            self._require_publisher(db, identity)
            row = db.execute("SELECT * FROM anchor_checkpoint WHERE singleton = 1").fetchone()
        if row is None:
            return None
        checkpoint = AuthorizationAuditCheckpoint(
            row["event_id"], row["event_hash"], row["published_at"]
        )
        return StoredAuditCheckpoint(
            checkpoint, self._etag(row["event_id"], row["event_hash"], row["revision"])
        )

    def publish_checkpoint(
        self,
        publisher_identity: str,
        checkpoint: AuthorizationAuditCheckpoint,
        *,
        if_match: str | None,
        if_none_match: str | None,
    ) -> StoredAuditCheckpoint:
        identity = self._identity(publisher_identity, "publisher_identity")
        self._validate_checkpoint(checkpoint)
        if if_match is not None and (
            not isinstance(if_match, str)
            or len(if_match) > 256
            or if_match.startswith("W/")
        ):
            raise ValueError("audit_anchor_if_match_invalid")
        if if_none_match not in (None, "*"):
            raise ValueError("audit_anchor_if_none_match_invalid")
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._require_publisher(db, identity)
            current = db.execute(
                "SELECT * FROM anchor_checkpoint WHERE singleton = 1"
            ).fetchone()
            if current is None:
                if if_none_match != "*" or if_match is not None:
                    raise AuditAnchorConflict("audit_anchor_create_precondition_failed")
                revision = 1
            else:
                current_etag = self._etag(
                    current["event_id"], current["event_hash"], current["revision"]
                )
                if if_match != current_etag or if_none_match is not None:
                    raise AuditAnchorConflict("audit_anchor_compare_and_set_conflict")
                if checkpoint.event_id < current["event_id"]:
                    raise AuditAnchorConflict("audit_anchor_event_id_regression")
                if checkpoint.event_id == current["event_id"]:
                    if not hmac.compare_digest(checkpoint.event_hash, current["event_hash"]):
                        raise AuditAnchorConflict("audit_anchor_same_event_hash_conflict")
                    stored = AuthorizationAuditCheckpoint(
                        current["event_id"], current["event_hash"], current["published_at"]
                    )
                    return StoredAuditCheckpoint(stored, current_etag)
                if hmac.compare_digest(checkpoint.event_hash, current["event_hash"]):
                    raise AuditAnchorConflict("audit_anchor_hash_did_not_advance")
                revision = current["revision"] + 1
            db.execute(
                """
                INSERT INTO anchor_checkpoint(singleton, event_id, event_hash, published_at, revision)
                VALUES(1, ?, ?, ?, ?)
                ON CONFLICT(singleton) DO UPDATE SET
                    event_id = excluded.event_id,
                    event_hash = excluded.event_hash,
                    published_at = excluded.published_at,
                    revision = excluded.revision
                """,
                (checkpoint.event_id, checkpoint.event_hash, now, revision),
            )
            etag = self._etag(checkpoint.event_id, checkpoint.event_hash, revision)
            db.commit()
        return StoredAuditCheckpoint(
            AuthorizationAuditCheckpoint(checkpoint.event_id, checkpoint.event_hash, now),
            etag,
        )

    def set_publisher(
        self, administrator_identity: str, publisher_identity: str, *, enabled: bool
    ) -> None:
        administrator = self._identity(administrator_identity, "administrator_identity")
        publisher = self._identity(publisher_identity, "publisher_identity")
        if type(enabled) is not bool:
            raise ValueError("publisher_enabled_must_be_boolean")
        if self.authorize_administrator(administrator) is not True:
            raise AuditAnchorUnauthorized("audit_anchor_administrator_not_authorized")
        now = datetime.now(timezone.utc).isoformat()
        operation = "enable" if enabled else "revoke"
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                """
                INSERT INTO anchor_publishers(identity, active, updated_at, updated_by)
                VALUES(?, ?, ?, ?)
                ON CONFLICT(identity) DO UPDATE SET
                    active = excluded.active,
                    updated_at = excluded.updated_at,
                    updated_by = excluded.updated_by
                """,
                (publisher, int(enabled), now, administrator),
            )
            db.execute(
                """
                INSERT INTO anchor_admin_audit(
                    administrator, publisher_identity, operation, occurred_at
                ) VALUES(?, ?, ?, ?)
                """,
                (administrator, publisher, operation, now),
            )
            db.commit()

    def administrative_audit(self, administrator_identity: str) -> tuple[dict, ...]:
        administrator = self._identity(administrator_identity, "administrator_identity")
        if self.authorize_administrator(administrator) is not True:
            raise AuditAnchorUnauthorized("audit_anchor_administrator_not_authorized")
        with self._connect() as db:
            rows = db.execute(
                """
                SELECT administrator, publisher_identity, operation, occurred_at
                FROM anchor_admin_audit ORDER BY audit_id
                """
            ).fetchall()
        return tuple(dict(row) for row in rows)


class AuditAnchorASGI:
    """Small ASGI endpoint; authenticate_scope must verify mTLS/service identity.

    The authenticator returns a trusted publisher identity or None. It must not
    derive identity from a JSON body or an untrusted forwarding header.
    """

    def __init__(self, service: SQLiteAuditAnchorService, authenticate_scope):
        if not isinstance(service, SQLiteAuditAnchorService):
            raise TypeError("sqlite_audit_anchor_service_required")
        if not callable(authenticate_scope):
            raise TypeError("trusted_scope_authenticator_required")
        self.service = service
        self.authenticate_scope = authenticate_scope

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http" or scope.get("path") != "/v1/checkpoint":
            await self._respond(send, 404, {"error": "not_found"})
            return
        method = scope.get("method", "GET").upper()
        if method not in {"GET", "PUT"}:
            await self._respond(send, 405, {"error": "method_not_allowed"}, [(b"allow", b"GET, PUT")])
            return
        try:
            identity = self.authenticate_scope(scope)
            if not isinstance(identity, str):
                await self._respond(send, 401, {"error": "unauthorized"})
                return
            if method == "GET":
                stored = self.service.get_checkpoint(identity)
                if stored is None:
                    await self._respond(send, 404, {"error": "not_provisioned"})
                    return
                await self._respond(
                    send, 200, self._payload(stored.checkpoint),
                    [(b"etag", stored.etag.encode("ascii"))],
                )
                return
            body = await self._read_body(receive)
            payload = self._decode_request(body)
            headers = {}
            for key, value in scope.get("headers", []):
                normalized = key.lower()
                if normalized in {b"if-match", b"if-none-match"} and normalized in headers:
                    raise ValueError("duplicate_conditional_header")
                headers[normalized] = value
            if_match = headers.get(b"if-match")
            if_none_match = headers.get(b"if-none-match")
            stored = self.service.publish_checkpoint(
                identity,
                AuthorizationAuditCheckpoint(payload["event_id"], payload["event_hash"]),
                if_match=if_match.decode("ascii") if if_match is not None else None,
                if_none_match=if_none_match.decode("ascii") if if_none_match is not None else None,
            )
            await self._respond(
                send, 200, self._payload(stored.checkpoint),
                [(b"etag", stored.etag.encode("ascii"))],
            )
        except AuditAnchorUnauthorized:
            await self._respond(send, 403, {"error": "forbidden"})
        except AuditAnchorConflict:
            await self._respond(send, 412, {"error": "checkpoint_conflict"})
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
            await self._respond(send, 400, {"error": "invalid_request"})
        except Exception:
            await self._respond(send, 503, {"error": "service_unavailable"})

    @staticmethod
    async def _read_body(receive) -> bytes:
        body = bytearray()
        while True:
            message = await receive()
            if message.get("type") != "http.request":
                raise ValueError("invalid_asgi_request")
            body.extend(message.get("body", b""))
            if len(body) > 4096:
                raise ValueError("request_too_large")
            if not message.get("more_body", False):
                return bytes(body)

    @staticmethod
    def _decode_request(body: bytes) -> dict:
        def no_duplicates(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("duplicate_json_member")
                result[key] = value
            return result
        payload = json.loads(body.decode("utf-8"), object_pairs_hook=no_duplicates)
        if (
            not isinstance(payload, dict)
            or set(payload) != {"event_id", "event_hash"}
            or type(payload["event_id"]) is not int
            or payload["event_id"] < 0
            or not isinstance(payload["event_hash"], str)
            or len(payload["event_hash"]) != 64
            or any(char not in "0123456789abcdef" for char in payload["event_hash"])
        ):
            raise ValueError("invalid_checkpoint_payload")
        return payload

    @staticmethod
    def _payload(checkpoint: AuthorizationAuditCheckpoint) -> dict:
        return {
            "event_id": checkpoint.event_id,
            "event_hash": checkpoint.event_hash,
            "published_at": checkpoint.published_at,
        }

    @staticmethod
    async def _respond(send, status: int, payload: dict, extra_headers=()):
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        headers = [
            (b"content-type", b"application/json; charset=utf-8"),
            (b"cache-control", b"no-store"),
            (b"x-content-type-options", b"nosniff"),
            (b"content-length", str(len(body)).encode("ascii")),
            *extra_headers,
        ]
        await send({"type": "http.response.start", "status": status, "headers": headers})
        await send({"type": "http.response.body", "body": body})
