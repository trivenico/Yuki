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
            db.execute("""CREATE TABLE IF NOT EXISTS lan_call_audit (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT, event_type TEXT NOT NULL,
                session_hash TEXT NOT NULL, owner_hash TEXT NOT NULL,
                actor_device_hash TEXT NOT NULL, created_at TEXT NOT NULL,
                previous_hash TEXT NOT NULL, event_hash TEXT NOT NULL
            )""")
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

    @staticmethod
    def _session_key(session_id):
        if not isinstance(session_id, str) or not 32 <= len(session_id) <= 128:
            raise CallSessionError("session_id_invalid")
        # Incoming-call polling exposes only the stored 256-bit digest as the
        # recipient's opaque acceptance handle; the original secret is never recovered.
        if len(session_id) == 64 and all(ch in "0123456789abcdef" for ch in session_id):
            return session_id
        return _digest(session_id)

    @staticmethod
    def _append_audit(db, event_type, session_hash, owner_id, actor_device_id):
        created_at = _stamp(_now())
        owner_hash = _digest(owner_id)
        actor_hash = _digest(actor_device_id)
        previous = db.execute(
            "SELECT event_hash FROM lan_call_audit ORDER BY event_id DESC LIMIT 1"
        ).fetchone()
        previous_hash = previous[0] if previous else "0" * 64
        payload = {
            "event_type": event_type, "session_hash": session_hash,
            "owner_hash": owner_hash, "actor_device_hash": actor_hash,
            "created_at": created_at, "previous_hash": previous_hash,
        }
        event_hash = hashlib.sha256(
            __import__("json").dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        db.execute("""INSERT INTO lan_call_audit
            (event_type,session_hash,owner_hash,actor_device_hash,created_at,previous_hash,event_hash)
            VALUES(?,?,?,?,?,?,?)""",
            (event_type, session_hash, owner_hash, actor_hash, created_at, previous_hash, event_hash))

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
            """, (_digest(session_id), caller.owner_id, caller.device_id,
                  recipient_device_id, "ringing", _stamp(now), _stamp(expires)))
            row = db.execute("SELECT * FROM lan_call_sessions WHERE session_hash=?",
                             (self._session_key(session_id),)).fetchone()
            self._append_audit(db, "invited", _digest(session_id), caller.owner_id, caller.device_id)
            return self._session(row, session_id)

    def invite_as_owner(self, *, owner_id: str, caller_device_id: str,
                        recipient_device_id: str, owner_confirmed: bool) -> CallSession:
        """Create an invitation from an authenticated owner's explicit UI action."""
        if owner_confirmed is not True:
            raise CallSessionDenied("owner_confirmation_required")
        if not isinstance(owner_id, str) or not 1 <= len(owner_id) <= 128 or not owner_id.strip():
            raise CallSessionError("owner_id_invalid")
        for value in (caller_device_id, recipient_device_id):
            if not isinstance(value, str) or not 1 <= len(value) <= 128:
                raise CallSessionError("device_id_invalid")
        if caller_device_id == recipient_device_id:
            raise CallSessionError("cannot_call_same_device")
        session_id = secrets.token_urlsafe(32)
        now = _now()
        expires = now + timedelta(seconds=self.invitation_ttl_seconds)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            caller = db.execute("""SELECT device_id FROM paired_devices
                WHERE device_id=? AND owner_id=? AND revoked_at IS NULL""",
                (caller_device_id, owner_id)).fetchone()
            recipient = db.execute("""SELECT device_id FROM paired_devices
                WHERE device_id=? AND owner_id=? AND revoked_at IS NULL""",
                (recipient_device_id, owner_id)).fetchone()
            if caller is None or recipient is None:
                raise CallSessionDenied("active_same_owner_devices_required")
            db.execute("""UPDATE lan_call_sessions SET status='expired',ended_at=?
                WHERE owner_id=? AND status='ringing' AND expires_at<=?""",
                (_stamp(now), owner_id, _stamp(now)))
            active = db.execute("""SELECT COUNT(*) FROM lan_call_sessions
                WHERE owner_id=? AND status='ringing' AND expires_at>?""",
                (owner_id, _stamp(now))).fetchone()[0]
            if active >= 10:
                raise CallSessionDenied("active_invitation_limit")
            session_hash = _digest(session_id)
            db.execute("""INSERT INTO lan_call_sessions
                (session_hash,owner_id,caller_device_id,recipient_device_id,status,created_at,expires_at)
                VALUES(?,?,?,?,?,?,?)""",
                (session_hash, owner_id, caller_device_id, recipient_device_id,
                 "ringing", _stamp(now), _stamp(expires)))
            self._append_audit(db, "invited", session_hash, owner_id, caller_device_id)
            row = db.execute("SELECT * FROM lan_call_sessions WHERE session_hash=?",
                             (session_hash,)).fetchone()
            return self._session(row, session_id)

    def list_incoming(self, *, device_token: str):
        """Return bounded pending invitations for the authenticated recipient device."""
        now = _stamp(_now())
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            device = self._authenticate(db, device_token)
            db.execute("""UPDATE lan_call_sessions SET status='expired',ended_at=?
                WHERE recipient_device_id=? AND status='ringing' AND expires_at<=?""",
                (now, device.device_id, now))
            rows = db.execute("""SELECT session_hash,caller_device_id,created_at,expires_at
                FROM lan_call_sessions WHERE recipient_device_id=? AND status='ringing'
                AND expires_at>? ORDER BY created_at ASC LIMIT 20""",
                (device.device_id, now)).fetchall()
            return [{
                "session_id": row["session_hash"],
                "caller_device_id": row["caller_device_id"],
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
                             (self._session_key(session_id),)).fetchone()
            if row is None or row["owner_id"] != device.owner_id:
                raise CallSessionDenied("call_session_not_found")
            participant = row["caller_device_id"] if required_role == "caller" else row["recipient_device_id"]
            if participant != device.device_id:
                raise CallSessionDenied("call_participant_required")
            if row["status"] not in allowed_statuses:
                raise CallSessionDenied("call_state_conflict")
            if row["status"] == "ringing" and row["expires_at"] <= _stamp(now):
                db.execute("""UPDATE lan_call_sessions SET status='expired',ended_at=?
                    WHERE session_hash=? AND status='ringing'""",
                    (_stamp(now), _digest(session_id)))
                expired = db.execute("SELECT * FROM lan_call_sessions WHERE session_hash=?",
                                     (_digest(session_id),)).fetchone()
                return self._session(expired, session_id)
            accepted_at = _stamp(now) if set_accepted else row["accepted_at"]
            ended_at = _stamp(now) if target_status in {"rejected", "ended"} else row["ended_at"]
            db.execute("""UPDATE lan_call_sessions
                SET status=?,accepted_at=?,ended_at=?
                WHERE session_hash=? AND status=?""",
                (target_status, accepted_at, ended_at, _digest(session_id), row["status"]))
            updated = db.execute("SELECT * FROM lan_call_sessions WHERE session_hash=?",
                                 (_digest(session_id),)).fetchone()
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
                             (_digest(session_id),)).fetchone()
            if row is None or row["owner_id"] != device.owner_id:
                raise CallSessionDenied("call_session_not_found")
            if device.device_id not in {row["caller_device_id"], row["recipient_device_id"]}:
                raise CallSessionDenied("call_participant_required")
            if row["status"] not in {"ringing", "accepted"}:
                raise CallSessionDenied("call_state_conflict")
            status = "expired" if row["status"] == "ringing" and row["expires_at"] <= _stamp(now) else "ended"
            db.execute("""UPDATE lan_call_sessions SET status=?,ended_at=?
                WHERE session_hash=? AND status=?""",
                (status, _stamp(now), _digest(session_id), row["status"]))
            updated = db.execute("SELECT * FROM lan_call_sessions WHERE session_hash=?",
                                 (_digest(session_id),)).fetchone()
            return self._session(updated, session_id)

    def get(self, *, session_id: str, device_token: str) -> CallSession:
        if not isinstance(session_id, str) or not 32 <= len(session_id) <= 128:
            raise CallSessionError("session_id_invalid")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            device = self._authenticate(db, device_token)
            row = db.execute("SELECT * FROM lan_call_sessions WHERE session_hash=?",
                             (_digest(session_id),)).fetchone()
            if row is None or row["owner_id"] != device.owner_id or device.device_id not in {
                row["caller_device_id"], row["recipient_device_id"]
            }:
                raise CallSessionDenied("call_participant_required")
            if row["status"] == "ringing" and row["expires_at"] <= _stamp(_now()):
                db.execute("""UPDATE lan_call_sessions SET status='expired',ended_at=?
                    WHERE session_hash=? AND status='ringing'""",
                    (_stamp(_now()), _digest(session_id)))
                row = db.execute("SELECT * FROM lan_call_sessions WHERE session_hash=?",
                                 (_digest(session_id),)).fetchone()
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
