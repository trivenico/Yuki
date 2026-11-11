"""Durable, consent-gated LAN call invitations; signaling state only, no media transport."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import secrets
import sqlite3



class CallSessionError(ValueError):
    """Invalid call session request."""


class CallSessionDenied(PermissionError):
    """Caller is not an active participant or consent is missing."""


@dataclass(frozen=True)
class CallSession:
    session_id: str
    owner_id: str
    caller_device_id: str
    recipient_device_id: str
    status: str
    created_at: str
    expires_at: str
    accepted_at: str | None
    ended_at: str | None


def _now():
    return datetime.now(timezone.utc)


def _stamp(value):
    return value.astimezone(timezone.utc).isoformat()


def _digest(value):
    return hashlib.sha256(value.encode("ascii")).hexdigest()


def _session_digest(value):
    if not isinstance(value, str) or not 32 <= len(value) <= 128:
        raise CallSessionError("session_id_invalid")
    if len(value) == 64 and all(ch in "0123456789abcdef" for ch in value):
        return value
    return _digest(value)


class LANCallSessionStore:
    """Persist call invitation/consent state for two already-paired devices.

    A call invitation is not a media connection. This class never opens sockets,
    starts microphones/cameras, or creates WebRTC peer connections. Device
    bearer credentials are verified against the pairing table in the same
    SQLite transaction as every state transition.
    """

    _STATUSES = frozenset({"ringing", "accepted", "rejected", "ended", "expired"})

    def __init__(self, pairing_service, *, invitation_ttl_seconds: int = 90):
        path = getattr(pairing_service, "database_path", None)
        if not isinstance(path, str) or not path:
            raise ValueError("pairing_service_database_required")
        if type(invitation_ttl_seconds) is not int or not 15 <= invitation_ttl_seconds <= 300:
            raise ValueError("invitation_ttl_seconds_invalid")
        self.database_path = path
        self.invitation_ttl_seconds = invitation_ttl_seconds
        with self._connect() as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS lan_call_sessions (
                    session_hash TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    caller_device_id TEXT NOT NULL,
                    recipient_device_id TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN
                        ('ringing','accepted','rejected','ended','expired')),
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    accepted_at TEXT,
                    ended_at TEXT,
                    FOREIGN KEY(caller_device_id) REFERENCES paired_devices(device_id),
                    FOREIGN KEY(recipient_device_id) REFERENCES paired_devices(device_id)
                )
            """)
            db.execute("""CREATE INDEX IF NOT EXISTS idx_lan_calls_owner_created
                ON lan_call_sessions(owner_id, created_at)""")
        self._pairing = pairing_service

    def _connect(self):
        db = sqlite3.connect(self.database_path, timeout=5.0, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=5000")
        db.execute("PRAGMA foreign_keys=ON")
        return _SQLiteConnection(db)

    @staticmethod
    def _session(row, session_id):
        return CallSession(
            session_id=session_id, owner_id=row["owner_id"],
            caller_device_id=row["caller_device_id"],
            recipient_device_id=row["recipient_device_id"],
            status=row["status"], created_at=row["created_at"],
            expires_at=row["expires_at"], accepted_at=row["accepted_at"],
            ended_at=row["ended_at"],
        )

    def _authenticate(self, db, device_token):
        device = self._pairing.authenticate_device_in_transaction(db, device_token)
        if device is None:
            raise CallSessionDenied("active_paired_device_required")
        return device

    def invite(self, *, caller_token: str, recipient_device_id: str,
               owner_confirmed: bool) -> CallSession:
        """Create a short-lived invitation; caller must be the authenticated owner-approved device."""
        if owner_confirmed is not True:
            raise CallSessionDenied("owner_confirmation_required")
        if not isinstance(recipient_device_id, str) or not 1 <= len(recipient_device_id) <= 128:
            raise CallSessionError("recipient_device_id_invalid")
        session_id = secrets.token_urlsafe(32)
        now = _now()
        expires = now + timedelta(seconds=self.invitation_ttl_seconds)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            caller = self._authenticate(db, caller_token)
            recipient = db.execute("""
                SELECT device_id FROM paired_devices
                WHERE device_id=? AND owner_id=? AND revoked_at IS NULL
            """, (recipient_device_id, caller.owner_id)).fetchone()
            if recipient is None:
                raise CallSessionDenied("active_same_owner_recipient_required")
            if caller.device_id == recipient_device_id:
                raise CallSessionError("cannot_call_same_device")
            # Bound active invitations per owner to avoid notification flooding.
            db.execute("""UPDATE lan_call_sessions SET status='expired', ended_at=?
                WHERE owner_id=? AND status='ringing' AND expires_at<=?""",
                (_stamp(now), caller.owner_id, _stamp(now)))
            active = db.execute("""SELECT COUNT(*) FROM lan_call_sessions
                WHERE owner_id=? AND status='ringing' AND expires_at>?""",
                (caller.owner_id, _stamp(now))).fetchone()[0]
            if active >= 10:
                raise CallSessionDenied("active_invitation_limit")
            db.execute("""
                INSERT INTO lan_call_sessions
                (session_hash,owner_id,caller_device_id,recipient_device_id,status,
                 created_at,expires_at)
                VALUES(?,?,?,?,?,?,?)
            """, (_session_digest(session_id), caller.owner_id, caller.device_id,
                  recipient_device_id, "ringing", _stamp(now), _stamp(expires)))
            row = db.execute("SELECT * FROM lan_call_sessions WHERE session_hash=?",
                             (_digest(session_id),)).fetchone()
            return self._session(row, session_id)


    def invite_as_owner(self, *, owner_id: str, caller_device_id: str,
                        recipient_device_id: str) -> CallSession:
        """Create an invitation after the owner API has consumed its one-time consent.

        Both devices must be active and belong to the authenticated owner. This
        method intentionally accepts no caller-supplied consent boolean.
        """
        for value, field in ((owner_id, "owner_id"),
                             (caller_device_id, "caller_device_id"),
                             (recipient_device_id, "recipient_device_id")):
            if not isinstance(value, str) or not value or len(value) > 128 or value.strip() != value:
                raise CallSessionError(f"{field}_invalid")
        if caller_device_id == recipient_device_id:
            raise CallSessionError("cannot_call_same_device")
        session_id = secrets.token_urlsafe(32)
        now = _now()
        expires = now + timedelta(seconds=self.invitation_ttl_seconds)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            devices = db.execute("""
                SELECT device_id FROM paired_devices
                WHERE owner_id=? AND revoked_at IS NULL
                  AND device_id IN (?,?)
            """, (owner_id, caller_device_id, recipient_device_id)).fetchall()
            if len(devices) != 2:
                raise CallSessionDenied("active_same_owner_devices_required")
            db.execute("""UPDATE lan_call_sessions SET status='expired', ended_at=?
                WHERE owner_id=? AND status='ringing' AND expires_at<=?""",
                (_stamp(now), owner_id, _stamp(now)))
            active = db.execute("""SELECT COUNT(*) FROM lan_call_sessions
                WHERE owner_id=? AND status='ringing' AND expires_at>?""",
                (owner_id, _stamp(now))).fetchone()[0]
            if active >= 10:
                raise CallSessionDenied("active_invitation_limit")
            db.execute("""
                INSERT INTO lan_call_sessions
                (session_hash,owner_id,caller_device_id,recipient_device_id,status,
                 created_at,expires_at)
                VALUES(?,?,?,?,?,?,?)
            """, (_digest(session_id), owner_id, caller_device_id,
                  recipient_device_id, "ringing", _stamp(now), _stamp(expires)))
            row = db.execute("SELECT * FROM lan_call_sessions WHERE session_hash=?",
                             (_digest(session_id),)).fetchone()
            return self._session(row, session_id)


    def list_for_owner(self, *, owner_id: str, limit: int = 50) -> list[CallSession]:
        """Return a bounded, newest-first history for the authenticated owner."""
        if not isinstance(owner_id, str) or not owner_id or len(owner_id) > 128:
            raise CallSessionError("owner_id_invalid")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise CallSessionError("limit_invalid")
        now = _now()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                """UPDATE lan_call_sessions SET status='expired',ended_at=?
                   WHERE owner_id=? AND status='ringing' AND expires_at<=?""",
                (_stamp(now), owner_id, _stamp(now)),
            )
            rows = db.execute(
                """SELECT * FROM lan_call_sessions WHERE owner_id=?
                   ORDER BY created_at DESC LIMIT ?""",
                (owner_id, limit),
            ).fetchall()
            return [self._session(row, row["session_hash"]) for row in rows]

    def cancel_as_owner(self, *, owner_id: str, session_id: str) -> CallSession:
        """Allow the authenticated owner to cancel only their still-ringing invite."""
        if not isinstance(owner_id, str) or not owner_id or len(owner_id) > 128:
            raise CallSessionError("owner_id_invalid")
        now = _now()
        digest = _session_digest(session_id)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM lan_call_sessions WHERE session_hash=? AND owner_id=?",
                (digest, owner_id),
            ).fetchone()
            if row is None:
                raise CallSessionDenied("call_session_not_found")
            if row["status"] != "ringing":
                raise CallSessionDenied("only_ringing_invites_can_be_cancelled")
            status = "expired" if row["expires_at"] <= _stamp(now) else "ended"
            db.execute(
                """UPDATE lan_call_sessions SET status=?,ended_at=?
                   WHERE session_hash=? AND owner_id=? AND status='ringing'""",
                (status, _stamp(now), digest, owner_id),
            )
            updated = db.execute(
                "SELECT * FROM lan_call_sessions WHERE session_hash=?",
                (digest,),
            ).fetchone()
            return self._session(updated, session_id)

    def list_incoming(self, *, device_token: str) -> list[dict]:
        """Return unexpired invitations for the authenticated recipient."""
        now = _now()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            device = self._authenticate(db, device_token)
            db.execute("""UPDATE lan_call_sessions SET status='expired', ended_at=?
                WHERE recipient_device_id=? AND status='ringing' AND expires_at<=?""",
                (_stamp(now), device.device_id, _stamp(now)))
            rows = db.execute("""SELECT * FROM lan_call_sessions
                WHERE recipient_device_id=? AND owner_id=? AND status='ringing'
                  AND expires_at>? AND EXISTS (
                    SELECT 1 FROM paired_devices caller
                    WHERE caller.device_id=lan_call_sessions.caller_device_id
                      AND caller.owner_id=lan_call_sessions.owner_id
                      AND caller.revoked_at IS NULL
                  )
                ORDER BY created_at ASC""",
                (device.device_id, device.owner_id, _stamp(now))).fetchall()
            return [{
                "session_id": row["session_hash"],
                "caller_device_id": row["caller_device_id"],
                "recipient_device_id": row["recipient_device_id"],
                "status": row["status"],
                "created_at": row["created_at"],
                "expires_at": row["expires_at"],
            } for row in rows]

    def _transition(self, *, session_id, device_token, allowed_statuses, target_status,
                    required_role, set_accepted=False):
        if not isinstance(session_id, str) or not 32 <= len(session_id) <= 128:
            raise CallSessionError("session_id_invalid")
        now = _now()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            device = self._authenticate(db, device_token)
            row = db.execute("SELECT * FROM lan_call_sessions WHERE session_hash=?",
                             (_session_digest(session_id),)).fetchone()
            if row is None or row["owner_id"] != device.owner_id:
                raise CallSessionDenied("call_session_not_found")
            if required_role == "recipient" and target_status == "accepted":
                caller_active = db.execute(
                    """SELECT 1 FROM paired_devices
                       WHERE owner_id=? AND device_id=? AND revoked_at IS NULL""",
                    (row["owner_id"], row["caller_device_id"]),
                ).fetchone()
                if caller_active is None:
                    raise CallSessionDenied("caller_device_inactive")
            participant = row["caller_device_id"] if required_role == "caller" else row["recipient_device_id"]
            if participant != device.device_id:
                raise CallSessionDenied("call_participant_required")
            if row["status"] not in allowed_statuses:
                raise CallSessionDenied("call_state_conflict")
            if row["status"] == "ringing" and row["expires_at"] <= _stamp(now):
                db.execute("""UPDATE lan_call_sessions SET status='expired',ended_at=?
                    WHERE session_hash=? AND status='ringing'""",
                    (_stamp(now), _session_digest(session_id)))
                expired = db.execute("SELECT * FROM lan_call_sessions WHERE session_hash=?",
                                     (_session_digest(session_id),)).fetchone()
                return self._session(expired, session_id)
            accepted_at = _stamp(now) if set_accepted else row["accepted_at"]
            ended_at = _stamp(now) if target_status in {"rejected", "ended"} else row["ended_at"]
            db.execute("""UPDATE lan_call_sessions
                SET status=?,accepted_at=?,ended_at=?
                WHERE session_hash=? AND status=?""",
                (target_status, accepted_at, ended_at, _session_digest(session_id), row["status"]))
            updated = db.execute("SELECT * FROM lan_call_sessions WHERE session_hash=?",
                                 (_session_digest(session_id),)).fetchone()
            return self._session(updated, session_id)

    def accept(self, *, session_id: str, recipient_token: str) -> CallSession:
        """Recipient's authenticated action is the explicit consent to accept."""
        return self._transition(session_id=session_id, device_token=recipient_token,
            allowed_statuses={"ringing"}, target_status="accepted",
            required_role="recipient", set_accepted=True)

    def reject(self, *, session_id: str, recipient_token: str) -> CallSession:
        return self._transition(session_id=session_id, device_token=recipient_token,
            allowed_statuses={"ringing"}, target_status="rejected",
            required_role="recipient")

    def end(self, *, session_id: str, device_token: str) -> CallSession:
        """Either authenticated participant may end an accepted call or cancel its invite."""
        if not isinstance(session_id, str) or not 32 <= len(session_id) <= 128:
            raise CallSessionError("session_id_invalid")
        now = _now()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            device = self._authenticate(db, device_token)
            row = db.execute("SELECT * FROM lan_call_sessions WHERE session_hash=?",
                             (_session_digest(session_id),)).fetchone()
            if row is None or row["owner_id"] != device.owner_id:
                raise CallSessionDenied("call_session_not_found")
            if device.device_id not in {row["caller_device_id"], row["recipient_device_id"]}:
                raise CallSessionDenied("call_participant_required")
            if row["status"] not in {"ringing", "accepted"}:
                raise CallSessionDenied("call_state_conflict")
            status = "expired" if row["status"] == "ringing" and row["expires_at"] <= _stamp(now) else "ended"
            db.execute("""UPDATE lan_call_sessions SET status=?,ended_at=?
                WHERE session_hash=? AND status=?""",
                (status, _stamp(now), _session_digest(session_id), row["status"]))
            updated = db.execute("SELECT * FROM lan_call_sessions WHERE session_hash=?",
                                 (_session_digest(session_id),)).fetchone()
            return self._session(updated, session_id)

    def get(self, *, session_id: str, device_token: str) -> CallSession:
        if not isinstance(session_id, str) or not 32 <= len(session_id) <= 128:
            raise CallSessionError("session_id_invalid")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            device = self._authenticate(db, device_token)
            row = db.execute("SELECT * FROM lan_call_sessions WHERE session_hash=?",
                             (_session_digest(session_id),)).fetchone()
            if row is None or row["owner_id"] != device.owner_id or device.device_id not in {
                row["caller_device_id"], row["recipient_device_id"]
            }:
                raise CallSessionDenied("call_participant_required")
            if row["status"] == "ringing" and row["expires_at"] <= _stamp(_now()):
                db.execute("""UPDATE lan_call_sessions SET status='expired',ended_at=?
                    WHERE session_hash=? AND status='ringing'""",
                    (_stamp(_now()), _session_digest(session_id)))
                row = db.execute("SELECT * FROM lan_call_sessions WHERE session_hash=?",
                                 (_session_digest(session_id),)).fetchone()
            return self._session(row, session_id)


class _SQLiteConnection:
    """Commit or roll back and always release the database handle."""

    def __init__(self, connection):
        self.connection = connection

    def __enter__(self):
        return self.connection

    def __exit__(self, exc_type, exc, tb):
        try:
            if exc_type is None:
                self.connection.commit()
            else:
                self.connection.rollback()
        finally:
            self.connection.close()
        return False
