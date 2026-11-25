"""Durable, metadata-minimizing audit sink for central authorization decisions.

Events are append-only through this API, serialized across processes by SQLite
BEGIN IMMEDIATE, and hash chained in sequence order. The chain is tamper-evident
against accidental or partial modification; a privileged attacker able to rewrite
the database can recompute it, so production deployments should periodically
anchor the head hash in an independently controlled system.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import ssl
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener
import math
import hashlib
import hmac
import json
import sqlite3
from typing import Protocol, runtime_checkable

from core.authorization import AuthorizationDecision, AuthorizationRequest
from core.sqlite_file_security import prepare_private_sqlite_path

_ZERO_HASH = "0" * 64


@dataclass(frozen=True)
class AuthorizationAuditCheckpoint:
    """Immutable checkpoint receipt returned by an independent anchor service."""

    event_id: int
    event_hash: str
    published_at: str = ""


@runtime_checkable
class AuthorizationAuditAnchor(Protocol):
    """Independent append-only checkpoint service; implementation is host supplied."""

    def publish(self, checkpoint: AuthorizationAuditCheckpoint) -> None: ...
    def latest(self) -> AuthorizationAuditCheckpoint | None: ...


@runtime_checkable
class AuthorizationAuditEvidenceVerifier(Protocol):
    """Host-supplied verifier backed by an independent decision evidence source.

    Implementations must authenticate the source independently of the audit
    database and bind every event in the proposed interval to the prior
    witnessed checkpoint and proposed checkpoint. Returning True based only on
    the supplied database rows is not an independent verification.
    """

    def verify_batch(
        self,
        previous: AuthorizationAuditCheckpoint,
        current: AuthorizationAuditCheckpoint,
        events: tuple[dict, ...],
    ) -> bool: ...


@runtime_checkable
class AuthorizationAuditRepository(Protocol):
    """Backend-neutral contract for the authoritative authorization audit chain."""

    def __call__(self, request: AuthorizationRequest, decision: AuthorizationDecision) -> int: ...
    def verify_chain(self) -> bool: ...
    def head_hash(self) -> str: ...
    def checkpoint(self) -> AuthorizationAuditCheckpoint: ...
    def verify_anchor(self, anchor: AuthorizationAuditAnchor, *, max_age_seconds: float | None = None, now: datetime | None = None) -> bool: ...
    def publish_checkpoint(self, anchor: AuthorizationAuditAnchor) -> AuthorizationAuditCheckpoint: ...
    def event_page(self, *, after_event_id: int = 0, limit: int = 500) -> tuple[dict, ...]: ...
    def close(self) -> None: ...


class _RejectAuditAnchorRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RuntimeError("audit_anchor_redirect_rejected")


class HttpsAuthorizationAuditAnchor:
    """mTLS-authenticated HTTPS anchor client using server-side ETag compare-and-set.

    The remote service must atomically implement GET plus conditional PUT:
    - GET returns 404 for an uninitialized anchor, otherwise JSON checkpoint and ETag.
    - PUT requires If-None-Match: * for first publication or If-Match: <etag>
      for updates; the server rejects stale ETags with HTTP 412.
    - The server, not this client, stamps published_at and enforces monotonic
      event_id/hash history. The response returns the committed checkpoint and
      a new ETag. No automatic retries are performed on conflicts.
    """

    def __init__(
        self,
        endpoint: str,
        *,
        ssl_context: ssl.SSLContext,
        bearer_token_provider,
        timeout_seconds: float = 5.0,
    ):
        if not isinstance(endpoint, str) or not endpoint or len(endpoint) > 2048:
            raise ValueError("audit_anchor_endpoint_must_be_trusted_https_url")
        parsed = urlsplit(endpoint)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("audit_anchor_endpoint_must_be_trusted_https_url")
        if not isinstance(ssl_context, ssl.SSLContext):
            raise TypeError("verified_tls_context_required")
        if ssl_context.verify_mode != ssl.CERT_REQUIRED or not ssl_context.check_hostname:
            raise ValueError("audit_anchor_tls_must_verify_certificate_and_hostname")
        if not callable(bearer_token_provider):
            raise TypeError("bearer_token_provider_required")
        if (
            isinstance(timeout_seconds, bool)
            or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(timeout_seconds)
            or timeout_seconds <= 0
            or timeout_seconds > 30
        ):
            raise ValueError("audit_anchor_timeout_must_be_between_0_and_30_seconds")
        self.endpoint = urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))
        self.ssl_context = ssl_context
        self.bearer_token_provider = bearer_token_provider
        self.timeout_seconds = float(timeout_seconds)
        self._opener = build_opener(HTTPSHandler(context=ssl_context), _RejectAuditAnchorRedirects())
        self._etag: str | None = None
        self._loaded = False

    @staticmethod
    def _valid_etag(value: object) -> bool:
        return (
            isinstance(value, str)
            and 2 <= len(value) <= 256
            and value.startswith(chr(34))
            and value.endswith(chr(34))
            and not value.startswith("W/")
            and value == value.strip()
            and not any(ord(char) < 33 or ord(char) > 126 for char in value)
        )

    @staticmethod
    def _decode_checkpoint(body: bytes) -> AuthorizationAuditCheckpoint:
        if not isinstance(body, bytes) or len(body) > 4096:
            raise ValueError("audit_anchor_response_too_large")
        def no_duplicates(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("duplicate_json_member")
                result[key] = value
            return result
        payload = json.loads(body.decode("utf-8"), object_pairs_hook=no_duplicates)
        if not isinstance(payload, dict) or set(payload) != {"event_id", "event_hash", "published_at"}:
            raise ValueError("malformed_audit_anchor_checkpoint")
        checkpoint = AuthorizationAuditCheckpoint(
            payload["event_id"], payload["event_hash"], payload["published_at"],
        )
        if (
            type(checkpoint.event_id) is not int
            or checkpoint.event_id < 0
            or not isinstance(checkpoint.event_hash, str)
            or len(checkpoint.event_hash) != 64
            or any(ch not in "0123456789abcdef" for ch in checkpoint.event_hash)
            or not isinstance(checkpoint.published_at, str)
            or not checkpoint.published_at
            or len(checkpoint.published_at) > 64
        ):
            raise ValueError("malformed_audit_anchor_checkpoint")
        timestamp = datetime.fromisoformat(checkpoint.published_at)
        if timestamp.tzinfo is None:
            raise ValueError("audit_anchor_timestamp_must_be_timezone_aware")
        return checkpoint

    def _send(self, method: str, *, body: bytes | None = None, etag: str | None = None):
        token = self.bearer_token_provider()
        if not isinstance(token, str) or not token or len(token) > 4096 or any(
            ord(ch) < 33 or ord(ch) > 126 for ch in token
        ):
            raise RuntimeError("audit_anchor_credentials_unavailable")
        headers = {
            "Accept": "application/json",
            "Authorization": "Bearer " + token,
            "Cache-Control": "no-store",
        }
        if body is not None:
            headers["Content-Type"] = "application/json"
        if etag is None and method == "PUT":
            headers["If-None-Match"] = "*"
        elif etag is not None:
            headers["If-Match"] = etag
        request = Request(self.endpoint, data=body, headers=headers, method=method)
        try:
            with self._opener.open(request, timeout=self.timeout_seconds) as response:
                payload = response.read(4097)
                response_etag = response.headers.get("ETag")
                status = response.status
        except HTTPError as exc:
            if exc.code == 404 and method == "GET":
                self._etag = None
                self._loaded = True
                return None, None
            if exc.code == 412:
                self._etag = None
                self._loaded = False
                raise RuntimeError("audit_anchor_compare_and_set_conflict") from exc
            raise RuntimeError("audit_anchor_remote_request_failed") from exc
        except (URLError, TimeoutError, OSError, RuntimeError) as exc:
            if str(exc) == "audit_anchor_redirect_rejected":
                raise RuntimeError("audit_anchor_redirect_rejected") from exc
            raise RuntimeError("audit_anchor_remote_unavailable") from exc
        if status not in (200, 201):
            raise RuntimeError("audit_anchor_unexpected_http_status")
        if not self._valid_etag(response_etag):
            raise RuntimeError("audit_anchor_missing_or_invalid_etag")
        checkpoint = self._decode_checkpoint(payload)
        return checkpoint, response_etag

    def latest(self) -> AuthorizationAuditCheckpoint | None:
        checkpoint, etag = self._send("GET")
        self._etag = etag
        self._loaded = True
        return checkpoint

    def publish(self, checkpoint: AuthorizationAuditCheckpoint) -> None:
        if not isinstance(checkpoint, AuthorizationAuditCheckpoint):
            raise TypeError("authorization_audit_checkpoint_required")
        if (
            type(checkpoint.event_id) is not int
            or checkpoint.event_id < 0
            or not isinstance(checkpoint.event_hash, str)
            or len(checkpoint.event_hash) != 64
            or any(ch not in "0123456789abcdef" for ch in checkpoint.event_hash)
        ):
            raise ValueError("malformed_authorization_audit_checkpoint")
        if not self._loaded:
            raise RuntimeError("audit_anchor_latest_must_be_read_before_publish")
        body = json.dumps(
            {"event_id": checkpoint.event_id, "event_hash": checkpoint.event_hash},
            separators=(",", ":"),
        ).encode("utf-8")
        committed, etag = self._send("PUT", body=body, etag=self._etag)
        if committed is None or committed.event_id != checkpoint.event_id or not hmac.compare_digest(
            committed.event_hash, checkpoint.event_hash
        ):
            self._etag = None
            self._loaded = False
            raise RuntimeError("audit_anchor_publication_receipt_mismatch")
        self._etag = etag
        self._loaded = True


class SQLiteAuthorizationAuditLog:
    """Cross-process durable authorization audit writer for production gateways."""

    def __init__(self, database_path: str):
        self.database_path = prepare_private_sqlite_path(database_path)
        with self._connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS authorization_audit_events (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                occurred_at TEXT NOT NULL,
                principal_hash TEXT NOT NULL,
                tenant_hash TEXT,
                agent_hash TEXT,
                action TEXT NOT NULL,
                resource_hash TEXT NOT NULL,
                task_hash TEXT,
                delegation_hash TEXT,
                approval_hash TEXT,
                risk TEXT NOT NULL,
                allowed INTEGER NOT NULL CHECK (allowed IN (0,1)),
                decision_hash TEXT NOT NULL,
                policy_revision_hash TEXT NOT NULL,
                reason_code TEXT NOT NULL,
                previous_hash TEXT NOT NULL,
                event_hash TEXT NOT NULL
            )""")
            db.execute("""CREATE INDEX IF NOT EXISTS authorization_audit_time_idx
                ON authorization_audit_events(event_id, occurred_at)""")

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.database_path, timeout=10.0, isolation_level=None)
        try:
            db.execute("PRAGMA busy_timeout=10000")
            yield db
        finally:
            db.close()

    @staticmethod
    def _digest(value: str | None) -> str | None:
        if value is None:
            return None
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    @staticmethod
    def _hash_event(event: dict) -> str:
        encoded = json.dumps(event, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    def __call__(self, request: AuthorizationRequest, decision: AuthorizationDecision) -> int:
        if not isinstance(request, AuthorizationRequest) or not isinstance(decision, AuthorizationDecision):
            raise TypeError("typed_authorization_audit_event_required")
        principal = request.principal
        occurred_at = datetime.now(timezone.utc).isoformat()
        values = {
            "occurred_at": occurred_at,
            "principal_hash": self._digest(principal.principal_id),
            "tenant_hash": self._digest(principal.tenant_id),
            "agent_hash": self._digest(principal.agent_id),
            "action": request.action,
            "resource_hash": self._digest(request.resource),
            "task_hash": self._digest(request.task_id),
            "delegation_hash": self._digest(request.delegation_id),
            "approval_hash": self._digest(request.approval_id),
            "risk": request.risk,
            "allowed": int(decision.allowed),
            "decision_hash": self._digest(decision.decision_id),
            "policy_revision_hash": self._digest(decision.policy_revision),
            "reason_code": decision.reason_code,
        }
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                previous = db.execute(
                    "SELECT event_hash FROM authorization_audit_events ORDER BY event_id DESC LIMIT 1"
                ).fetchone()
                previous_hash = previous[0] if previous else _ZERO_HASH
                event = dict(values, previous_hash=previous_hash)
                event_hash = self._hash_event(event)
                columns = tuple(values) + ("previous_hash", "event_hash")
                placeholders = ",".join("?" for _ in columns)
                cursor = db.execute(
                    "INSERT INTO authorization_audit_events (" + ",".join(columns) +
                    ") VALUES (" + placeholders + ")",
                    tuple(values[name] for name in values) + (previous_hash, event_hash),
                )
                db.execute("COMMIT")
                return int(cursor.lastrowid)
            except Exception:
                db.execute("ROLLBACK")
                raise

    def verify_chain(self) -> bool:
        columns = (
            "occurred_at", "principal_hash", "tenant_hash", "agent_hash", "action",
            "resource_hash", "task_hash", "delegation_hash", "approval_hash", "risk",
            "allowed", "decision_hash", "policy_revision_hash", "reason_code",
        )
        with self._connect() as db:
            rows = db.execute(
                "SELECT " + ",".join(columns) +
                ",previous_hash,event_hash FROM authorization_audit_events ORDER BY event_id ASC"
            ).fetchall()
        previous_hash = _ZERO_HASH
        for row in rows:
            values = dict(zip(columns, row[:len(columns)]))
            stored_previous, stored_hash = row[-2:]
            if not hmac.compare_digest(stored_previous, previous_hash):
                return False
            if self._hash_event(dict(values, previous_hash=stored_previous)) != stored_hash:
                return False
            previous_hash = stored_hash
        return True

    def head_hash(self) -> str:
        with self._connect() as db:
            row = db.execute(
                "SELECT event_hash FROM authorization_audit_events ORDER BY event_id DESC LIMIT 1"
            ).fetchone()
        return row[0] if row else _ZERO_HASH

    def event_page(self, *, after_event_id: int = 0, limit: int = 500) -> tuple[dict, ...]:
        """Read a bounded ordered page for security-monitor ingestion."""
        if type(after_event_id) is not int or after_event_id < 0 or type(limit) is not int or not 1 <= limit <= 5000:
            raise ValueError("authorization_audit_page_invalid")
        with self._connect() as db:
            rows = db.execute(
                "SELECT event_id, occurred_at, principal_hash, reason_code, allowed "
                "FROM authorization_audit_events WHERE event_id>? ORDER BY event_id LIMIT ?",
                (after_event_id, limit),
            ).fetchall()
        return tuple({
            "event_id": int(row[0]), "occurred_at": row[1], "principal_hash": row[2],
            "reason_code": row[3], "allowed": int(row[4]),
        } for row in rows)

    def close(self) -> None:
        """Compatibility lifecycle hook; SQLite connections are operation-scoped."""
        return None

    def checkpoint(self) -> AuthorizationAuditCheckpoint:
        """Capture a consistent local head for publication to an independent system."""
        with self._connect() as db:
            row = db.execute(
                "SELECT event_id, event_hash FROM authorization_audit_events ORDER BY event_id DESC LIMIT 1"
            ).fetchone()
        return AuthorizationAuditCheckpoint(int(row[0]), row[1], datetime.now(timezone.utc).isoformat()) if row else AuthorizationAuditCheckpoint(0, _ZERO_HASH, datetime.now(timezone.utc).isoformat())

    @classmethod
    def verify_backup(
        cls,
        backup_path: str,
        anchor: AuthorizationAuditAnchor,
        *,
        max_age_seconds: float | None = None,
        now: datetime | None = None,
    ) -> bool:
        """Verify a backup read-only against the external anchor before restore.

        This method never creates a database, repairs rows, publishes a checkpoint,
        or changes the caller's active audit path.
        """
        if not isinstance(anchor, AuthorizationAuditAnchor):
            raise TypeError("independent_authorization_audit_anchor_required")
        if max_age_seconds is not None and (
            isinstance(max_age_seconds, bool)
            or not isinstance(max_age_seconds, (int, float))
            or not math.isfinite(max_age_seconds)
            or max_age_seconds <= 0
        ):
            return False
        try:
            path = Path(backup_path).resolve(strict=True)
            if not path.is_file():
                return False
            uri = "file:" + quote(path.as_posix(), safe="/:") + "?mode=ro"
            with sqlite3.connect(uri, uri=True, timeout=5.0) as db:
                columns = (
                    "occurred_at", "principal_hash", "tenant_hash", "agent_hash", "action",
                    "resource_hash", "task_hash", "delegation_hash", "approval_hash", "risk",
                    "allowed", "decision_hash", "policy_revision_hash", "reason_code",
                )
                rows = db.execute(
                    "SELECT event_id," + ",".join(columns) +
                    ",previous_hash,event_hash FROM authorization_audit_events ORDER BY event_id ASC"
                ).fetchall()
        except (OSError, sqlite3.Error, ValueError):
            return False
        previous_hash = _ZERO_HASH
        head_id = 0
        anchored_hash = _ZERO_HASH
        for expected_id, row in enumerate(rows, start=1):
            event_id = row[0]
            if type(event_id) is not int or event_id != expected_id:
                return False
            values = dict(zip(columns, row[1:1 + len(columns)]))
            stored_previous, stored_hash = row[-2:]
            if not isinstance(stored_previous, str) or not hmac.compare_digest(stored_previous, previous_hash):
                return False
            if not isinstance(stored_hash, str) or not hmac.compare_digest(
                cls._hash_event(dict(values, previous_hash=stored_previous)), stored_hash
            ):
                return False
            previous_hash = stored_hash
            head_id = event_id
        try:
            checkpoint = anchor.latest()
        except Exception:
            return False
        if (
            not isinstance(checkpoint, AuthorizationAuditCheckpoint)
            or type(checkpoint.event_id) is not int
            or checkpoint.event_id < 0
            or not isinstance(checkpoint.event_hash, str)
            or len(checkpoint.event_hash) != 64
            or any(char not in "0123456789abcdef" for char in checkpoint.event_hash)
            or not isinstance(checkpoint.published_at, str)
            or not checkpoint.published_at
            or len(checkpoint.published_at) > 64
            or head_id < checkpoint.event_id
        ):
            return False
        if max_age_seconds is not None:
            try:
                published_at = datetime.fromisoformat(checkpoint.published_at)
                current_time = now or datetime.now(timezone.utc)
                if published_at.tzinfo is None or current_time.tzinfo is None:
                    return False
                age = (current_time - published_at).total_seconds()
            except (TypeError, ValueError, OverflowError):
                return False
            if age < -30 or age > max_age_seconds:
                return False
        if checkpoint.event_id == 0:
            anchored_hash = _ZERO_HASH
        else:
            anchored_hash = rows[checkpoint.event_id - 1][-1]
        return hmac.compare_digest(anchored_hash, checkpoint.event_hash)

    def publish_checkpoint(self, anchor: AuthorizationAuditAnchor) -> AuthorizationAuditCheckpoint:
        """Publish the current chain head; anchor must be independently controlled."""
        if not isinstance(anchor, AuthorizationAuditAnchor):
            raise TypeError("independent_authorization_audit_anchor_required")
        latest = anchor.latest()
        checkpoint = self.checkpoint()
        if latest is not None:
            if not isinstance(latest, AuthorizationAuditCheckpoint):
                raise RuntimeError("authorization_audit_anchor_malformed")
            if latest.event_id > checkpoint.event_id:
                raise RuntimeError("authorization_audit_local_history_behind_anchor")
            if latest.event_id == checkpoint.event_id and not hmac.compare_digest(
                latest.event_hash, checkpoint.event_hash
            ):
                raise RuntimeError("authorization_audit_anchor_head_mismatch")
            if not self.verify_anchor(anchor):
                raise RuntimeError("authorization_audit_anchor_integrity_mismatch")
        anchor.publish(checkpoint)
        published = anchor.latest()
        if (
            not isinstance(published, AuthorizationAuditCheckpoint)
            or published.event_id != checkpoint.event_id
            or not hmac.compare_digest(published.event_hash, checkpoint.event_hash)
        ):
            raise RuntimeError("authorization_audit_anchor_publication_unconfirmed")
        return published

    def verify_anchor(
        self,
        anchor: AuthorizationAuditAnchor,
        *,
        max_age_seconds: float | None = None,
        now: datetime | None = None,
    ) -> bool:
        """Verify the anchored prefix and, optionally, checkpoint publication freshness."""
        if not isinstance(anchor, AuthorizationAuditAnchor):
            raise TypeError("independent_authorization_audit_anchor_required")
        try:
            checkpoint = anchor.latest()
        except Exception:
            return False
        if checkpoint is None:
            return False
        if (
            not isinstance(checkpoint, AuthorizationAuditCheckpoint)
            or type(checkpoint.event_id) is not int
            or checkpoint.event_id < 0
            or not isinstance(checkpoint.event_hash, str)
            or len(checkpoint.event_hash) != 64
            or any(char not in "0123456789abcdef" for char in checkpoint.event_hash)
            or not isinstance(checkpoint.published_at, str)
            or not checkpoint.published_at
            or len(checkpoint.published_at) > 64
        ):
            return False
        if max_age_seconds is not None:
            if isinstance(max_age_seconds, bool) or not isinstance(max_age_seconds, (int, float)):
                return False
            if not math.isfinite(max_age_seconds) or max_age_seconds <= 0:
                return False
            try:
                published_at = datetime.fromisoformat(checkpoint.published_at)
                current_time = now or datetime.now(timezone.utc)
                if published_at.tzinfo is None or current_time.tzinfo is None:
                    return False
                age_seconds = (current_time - published_at).total_seconds()
            except (TypeError, ValueError, OverflowError):
                return False
            if age_seconds < -30 or age_seconds > max_age_seconds:
                return False
        if not self.verify_chain():
            return False
        if checkpoint.event_id == 0:
            return hmac.compare_digest(checkpoint.event_hash, _ZERO_HASH)
        with self._connect() as db:
            row = db.execute(
                "SELECT event_hash FROM authorization_audit_events WHERE event_id = ?",
                (checkpoint.event_id,),
            ).fetchone()
        return bool(row) and hmac.compare_digest(row[0], checkpoint.event_hash)


class AuthorizationAuditCheckpointPublisher:
    """One-shot publisher that requires independent evidence for new events.

    The host scheduler invokes run_once() at its configured interval. This
    publisher never bootstraps an anchor and never advances it merely because
    the local hash chain is internally consistent.
    """

    def __init__(
        self,
        audit_log: AuthorizationAuditRepository,
        anchor: AuthorizationAuditAnchor,
        evidence_verifier: AuthorizationAuditEvidenceVerifier,
    ):
        if not isinstance(audit_log, AuthorizationAuditRepository):
            raise TypeError("durable_authorization_audit_repository_required")
        if not isinstance(anchor, AuthorizationAuditAnchor):
            raise TypeError("independent_authorization_audit_anchor_required")
        if not isinstance(evidence_verifier, AuthorizationAuditEvidenceVerifier):
            raise TypeError("independent_authorization_audit_evidence_verifier_required")
        self.audit_log = audit_log
        self.anchor = anchor
        self.evidence_verifier = evidence_verifier

    def _verified_events(
        self,
        previous: AuthorizationAuditCheckpoint,
        current: AuthorizationAuditCheckpoint,
    ) -> tuple[dict, ...]:
        events = []
        cursor = previous.event_id
        while cursor < current.event_id:
            page = self.audit_log.event_page(after_event_id=cursor, limit=5000)
            if not isinstance(page, tuple) or not page:
                raise RuntimeError("authorization_audit_evidence_batch_incomplete")
            for event in page:
                if (
                    not isinstance(event, dict)
                    or type(event.get("event_id")) is not int
                    or event["event_id"] != cursor + 1
                    or not isinstance(event.get("occurred_at"), str)
                    or not isinstance(event.get("principal_hash"), str)
                    or not isinstance(event.get("reason_code"), str)
                    or type(event.get("allowed")) is not bool
                ):
                    raise RuntimeError("authorization_audit_evidence_batch_invalid")
                events.append(event)
                cursor = event["event_id"]
                if cursor == current.event_id:
                    break
            if len(page) < 5000 and cursor < current.event_id:
                raise RuntimeError("authorization_audit_evidence_batch_incomplete")
        return tuple(events)

    def run_once(self) -> AuthorizationAuditCheckpoint:
        """Verify independently sourced decision evidence before advancing."""
        try:
            latest = self.anchor.latest()
        except Exception as exc:
            raise RuntimeError("authorization_audit_anchor_unavailable") from exc
        if latest is None:
            raise RuntimeError("authorization_audit_anchor_requires_operator_bootstrap")
        if not isinstance(latest, AuthorizationAuditCheckpoint):
            raise RuntimeError("authorization_audit_anchor_malformed")
        if not self.audit_log.verify_anchor(self.anchor):
            raise RuntimeError("authorization_audit_anchor_integrity_mismatch")
        current = self.audit_log.checkpoint()
        if current.event_id < latest.event_id:
            raise RuntimeError("authorization_audit_local_history_behind_anchor")
        if current.event_id == latest.event_id:
            if not hmac.compare_digest(current.event_hash, latest.event_hash):
                raise RuntimeError("authorization_audit_anchor_head_mismatch")
        else:
            events = self._verified_events(latest, current)
            try:
                accepted = self.evidence_verifier.verify_batch(latest, current, events)
            except Exception as exc:
                raise RuntimeError("authorization_audit_independent_evidence_unavailable") from exc
            if accepted is not True:
                raise RuntimeError("authorization_audit_independent_evidence_rejected")
        self.anchor.publish(current)
        published = self.anchor.latest()
        if (
            not isinstance(published, AuthorizationAuditCheckpoint)
            or published.event_id != current.event_id
            or not hmac.compare_digest(published.event_hash, current.event_hash)
        ):
            raise RuntimeError("authorization_audit_anchor_publication_unconfirmed")
        return published
