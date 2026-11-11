"""One-time, session-bound owner confirmation challenges for sensitive LAN calls.

This store is a security primitive, not a user interface or authentication
system. The issuing API must first re-authenticate the owner with a fresh
credential check and derive owner/session identity from the authenticated
request. A model, agent, device credential, or request boolean must never be
allowed to issue a challenge.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import secrets
import sqlite3
from pathlib import Path


class CallConfirmationError(ValueError):
    """Malformed confirmation challenge request."""


class CallConfirmationDenied(PermissionError):
    """Challenge missing, expired, mismatched, or already consumed."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _stamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _valid_identifier(value: str, *, field: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 128 or value.strip() != value:
        raise CallConfirmationError(f"{field}_invalid")
    return value


class SQLiteCallConfirmationStore:
    """Persist opaque challenges bound to one owner session and exact call intent.

    The raw random challenge is returned once. Only its SHA-256 digest is stored.
    Consumption is transactional and one-time, so concurrent/replayed requests
    cannot authorize more than one invitation.
    """

    def __init__(self, database_path: str | Path, *, ttl_seconds: int = 90):
        if isinstance(ttl_seconds, bool) or not isinstance(ttl_seconds, int) or not 15 <= ttl_seconds <= 300:
            raise ValueError("ttl_seconds must be between 15 and 300")
        self.database_path = str(Path(database_path).expanduser())
        self.ttl_seconds = ttl_seconds
        Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("""
                CREATE TABLE IF NOT EXISTS call_confirmation_challenges (
                    token_hash TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    session_hash TEXT NOT NULL,
                    caller_device_id TEXT NOT NULL,
                    recipient_device_id TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    consumed_at TEXT
                )
            """)
            db.execute("""
                CREATE INDEX IF NOT EXISTS idx_call_confirmation_owner_expiry
                ON call_confirmation_challenges(owner_id, expires_at)
            """)

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.database_path, timeout=5.0, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=5000")
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def issue(
        self,
        *,
        owner_id: str,
        authenticated_session_token: str,
        caller_device_id: str,
        recipient_device_id: str,
    ) -> tuple[str, str]:
        """Issue only after the caller has independently re-authenticated the owner."""
        owner_id = _valid_identifier(owner_id, field="owner_id")
        caller_device_id = _valid_identifier(caller_device_id, field="caller_device_id")
        recipient_device_id = _valid_identifier(recipient_device_id, field="recipient_device_id")
        if caller_device_id == recipient_device_id:
            raise CallConfirmationError("same_device_not_allowed")
        if not isinstance(authenticated_session_token, str) or not 20 <= len(authenticated_session_token) <= 256:
            raise CallConfirmationDenied("authenticated_session_required")

        raw_token = secrets.token_urlsafe(32)
        now = _now()
        expires = now + timedelta(seconds=self.ttl_seconds)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "DELETE FROM call_confirmation_challenges WHERE expires_at<=? OR consumed_at IS NOT NULL",
                (_stamp(now),),
            )
            active = db.execute(
                """SELECT COUNT(*) FROM call_confirmation_challenges
                   WHERE owner_id=? AND consumed_at IS NULL AND expires_at>?""",
                (owner_id, _stamp(now)),
            ).fetchone()[0]
            if active >= 10:
                raise CallConfirmationDenied("active_confirmation_limit")
            db.execute(
                """INSERT INTO call_confirmation_challenges
                   (token_hash,owner_id,session_hash,caller_device_id,recipient_device_id,
                    created_at,expires_at)
                   VALUES(?,?,?,?,?,?,?)""",
                (_hash(raw_token), owner_id, _hash(authenticated_session_token),
                 caller_device_id, recipient_device_id, _stamp(now), _stamp(expires)),
            )
        return raw_token, _stamp(expires)

    def consume(
        self,
        *,
        token: str,
        owner_id: str,
        authenticated_session_token: str,
        caller_device_id: str,
        recipient_device_id: str,
    ) -> None:
        """Atomically consume the challenge for exactly the issued owner/session/call."""
        if not isinstance(token, str) or not 32 <= len(token) <= 128:
            raise CallConfirmationDenied("confirmation_required")
        owner_id = _valid_identifier(owner_id, field="owner_id")
        caller_device_id = _valid_identifier(caller_device_id, field="caller_device_id")
        recipient_device_id = _valid_identifier(recipient_device_id, field="recipient_device_id")
        if not isinstance(authenticated_session_token, str) or not 20 <= len(authenticated_session_token) <= 256:
            raise CallConfirmationDenied("authenticated_session_required")

        now = _stamp(_now())
        token_hash = _hash(token)
        session_hash = _hash(authenticated_session_token)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM call_confirmation_challenges WHERE token_hash=?",
                (token_hash,),
            ).fetchone()
            if row is None:
                raise CallConfirmationDenied("confirmation_invalid")
            expected = (
                row["owner_id"] == owner_id
                and hmac.compare_digest(row["session_hash"], session_hash)
                and row["caller_device_id"] == caller_device_id
                and row["recipient_device_id"] == recipient_device_id
                and row["consumed_at"] is None
                and row["expires_at"] > now
            )
            if not expected:
                raise CallConfirmationDenied("confirmation_invalid")
            changed = db.execute(
                """UPDATE call_confirmation_challenges SET consumed_at=?
                   WHERE token_hash=? AND consumed_at IS NULL AND expires_at>?""",
                (now, token_hash, now),
            ).rowcount
            if changed != 1:
                raise CallConfirmationDenied("confirmation_invalid")
