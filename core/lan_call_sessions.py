"""Durable, consent-gated LAN call invitations; signaling state only, no media transport."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
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
            db.execute("""
                CREATE TABLE IF NOT EXISTS lan_call_notifications (
                    notification_hash TEXT PRIMARY KEY,
                    session_hash TEXT NOT NULL,
                    owner_id TEXT NOT NULL,
                    recipient_device_id TEXT NOT NULL,
                    event_type TEXT NOT NULL CHECK(event_type='incoming_call'),
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    acknowledged_at TEXT,
                    UNIQUE(session_hash, event_type),
                    FOREIGN KEY(session_hash) REFERENCES lan_call_sessions(session_hash)
                )
            """)
            db.execute("""CREATE INDEX IF NOT EXISTS idx_lan_call_notifications_recipient
                ON lan_call_notifications(recipient_device_id, acknowledged_at, expires_at)""")
            db.execute("""CREATE TABLE IF NOT EXISTS lan_call_audit (
                event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT NOT NULL,
                session_hash TEXT NOT NULL,
                owner_hash TEXT NOT NULL,
                actor_device_hash TEXT NOT NULL,
                created_at TEXT NOT NULL,
                previous_hash TEXT NOT NULL,
                event_hash TEXT NOT NULL
            )""")
        self._pairing = pairing_service

    def _connect(self):
        db = sqlite3.connect(self.database_path, timeout=5.0, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=5000")
        db.execute("PRAGMA foreign_keys=ON")
        return _SQLiteConnection(db)

    @staticmethod
    def _append_audit(db, event_type, session_hash, owner_id, actor_device_id):
        """Append a tamper-evident event inside the state-change transaction."""
        created_at = _stamp(_now())
        owner_hash = _digest(owner_id)
        actor_hash = _digest(actor_device_id)
        previous = db.execute(
            "SELECT event_hash FROM lan_call_audit ORDER BY event_id DESC LIMIT 1"
        ).fetchone()
        previous_hash = previous[0] if previous else "0" * 64
        payload = {
            "event_type": event_type,
            "session_hash": session_hash,
            "owner_hash": owner_hash,
            "actor_device_hash": actor_hash,
            "created_at": created_at,
            "previous_hash": previous_hash,
        }
        event_hash = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        db.execute("""INSERT INTO lan_call_audit
            (event_type,session_hash,owner_hash,actor_device_hash,created_at,previous_hash,event_hash)
            VALUES(?,?,?,?,?,?,?)""",
            (event_type, session_hash, owner_hash, actor_hash, created_at, previous_hash, event_hash))

    def _expire_owner_invites(self, db, owner_id, actor_device_id, now,
                              recipient_device_id=None):
        stamp = _stamp(now)
        query = """SELECT session_hash FROM lan_call_sessions
                   WHERE owner_id=? AND status='ringing' AND expires_at<=?"""
        args = [owner_id, stamp]
        if recipient_device_id is not None:
            query += " AND recipient_device_id=?"
            args.append(recipient_device_id)
        rows = db.execute(query, args).fetchall()
        if not rows:
            return
        update = """UPDATE lan_call_sessions SET status='expired',ended_at=?
                    WHERE owner_id=? AND status='ringing' AND expires_at<=?"""
        update_args = [stamp, owner_id, stamp]
        if recipient_device_id is not None:
            update += " AND recipient_device_id=?"
            update_args.append(recipient_device_id)
        db.execute(update, update_args)
        for row in rows:
            self._append_audit(
                db, "expired", row["session_hash"], owner_id, actor_device_id
            )

    def verify_audit_chain(self):
        """Verify the append-only hash chain; detects tampering but is not external anchoring."""
        with self._connect() as db:
            rows = db.execute("""SELECT event_type,session_hash,owner_hash,
                actor_device_hash,created_at,previous_hash,event_hash
                FROM lan_call_audit ORDER BY event_id""").fetchall()
        previous_hash = "0" * 64
        for row in rows:
            if not hmac.compare_digest(row["previous_hash"], previous_hash):
                return False
            payload = {
                "event_type": row["event_type"],
                "session_hash": row["session_hash"],
                "owner_hash": row["owner_hash"],
                "actor_device_hash": row["actor_device_hash"],
                "created_at": row["created_at"],
                "previous_hash": row["previous_hash"],
            }
            expected = hashlib.sha256(
                json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest()
            if not hmac.compare_digest(expected, row["event_hash"]):
                return False
            previous_hash = row["event_hash"]
        return True

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
        """Reject the retired boolean-consent API.

        A caller-controlled boolean is not proof of owner intent. New invitation
        creation must flow through the authenticated owner API, which consumes
        a one-time challenge bound to the owner session and exact device pair.
        Kept temporarily to fail closed for older internal callers.
        """
        raise CallSessionDenied("owner_confirmation_challenge_required")

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
            self._expire_owner_invites(db, owner_id, caller_device_id, now)
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
            self._enqueue_invitation_notification(db, row, now)
            self._append_audit(db, "invited", _session_digest(session_id),
                               owner_id, caller_device_id)
            return self._session(row, session_id)


    @staticmethod
    def _enqueue_invitation_notification(db, session_row, now):
        """Write a minimal alert in the same transaction as invitation creation."""
        notification_id = secrets.token_urlsafe(32)
        db.execute("""
            INSERT INTO lan_call_notifications
            (notification_hash,session_hash,owner_id,recipient_device_id,event_type,
             created_at,expires_at)
            VALUES(?,?,?,?,?,?,?)
        """, (_digest(notification_id), session_row["session_hash"],
              session_row["owner_id"], session_row["recipient_device_id"],
              "incoming_call", session_row["created_at"], session_row["expires_at"]))

    def list_notifications(self, *, device_token: str, limit: int = 50) -> list[dict]:
        """Return unacknowledged, still-ringing alerts; polling is at-least-once."""
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise CallSessionError("limit_invalid")
        now = _now()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            device = self._authenticate(db, device_token)
            # Expired alerts are not audit records; purge them during inbox polling
            # so an offline device cannot leave an unbounded notification backlog.
            db.execute(
                "DELETE FROM lan_call_notifications WHERE expires_at<=?",
                (_stamp(now),),
            )
            rows = db.execute("""
                SELECT n.notification_hash,n.event_type,n.created_at,n.expires_at,
                       n.session_hash,s.caller_device_id,s.recipient_device_id
                FROM lan_call_notifications n
                JOIN lan_call_sessions s ON s.session_hash=n.session_hash
                JOIN paired_devices caller ON caller.device_id=s.caller_device_id
                WHERE n.recipient_device_id=? AND n.owner_id=?
                  AND n.acknowledged_at IS NULL AND n.expires_at>?
                  AND s.status='ringing' AND s.expires_at>?
                  AND caller.owner_id=n.owner_id AND caller.revoked_at IS NULL
                ORDER BY n.created_at ASC LIMIT ?
            """, (device.device_id, device.owner_id, _stamp(now), _stamp(now), limit)).fetchall()
            return [{
                "notification_id": row["notification_hash"],
                "event_type": row["event_type"],
                "created_at": row["created_at"],
                "expires_at": row["expires_at"],
                "session_id": row["session_hash"],
                "caller_device_id": row["caller_device_id"],
                "recipient_device_id": row["recipient_device_id"],
            } for row in rows]

    def acknowledge_notification(self, *, notification_id: str, device_token: str) -> bool:
        """Acknowledge only an alert owned by the authenticated recipient device."""
        if not isinstance(notification_id, str) or not (
            len(notification_id) == 64 and all(ch in "0123456789abcdef" for ch in notification_id)
        ):
            raise CallSessionError("notification_id_invalid")
        now = _now()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            device = self._authenticate(db, device_token)
            changed = db.execute("""
                UPDATE lan_call_notifications SET acknowledged_at=?
                WHERE notification_hash=? AND recipient_device_id=? AND owner_id=?
                  AND acknowledged_at IS NULL AND expires_at>?
            """, (_stamp(now), notification_id, device.device_id, device.owner_id,
                  _stamp(now))).rowcount
            if changed:
                return True
            existing = db.execute("""
                SELECT acknowledged_at,expires_at FROM lan_call_notifications
                WHERE notification_hash=? AND recipient_device_id=? AND owner_id=?
            """, (notification_id, device.device_id, device.owner_id)).fetchone()
            # Idempotent retry: an already acknowledged alert remains success.
            return bool(existing is not None and existing["acknowledged_at"] is not None)

    def list_for_owner(self, *, owner_id: str, limit: int = 50) -> list[CallSession]:
        """Return a bounded, newest-first history for the authenticated owner."""
        if not isinstance(owner_id, str) or not owner_id or len(owner_id) > 128:
            raise CallSessionError("owner_id_invalid")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise CallSessionError("limit_invalid")
        now = _now()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self._expire_owner_invites(db, owner_id, owner_id, now)
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
            self._append_audit(db, status, digest, owner_id, owner_id)
            return self._session(updated, session_id)

    def list_incoming(self, *, device_token: str) -> list[dict]:
        """Return unexpired invitations for the authenticated recipient."""
        now = _now()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            device = self._authenticate(db, device_token)
            self._expire_owner_invites(
                db, device.owner_id, device.device_id, now,
                recipient_device_id=device.device_id,
            )
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
                self._append_audit(db, "expired", _session_digest(session_id),
                                   device.owner_id, device.device_id)
                return self._session(expired, session_id)
            accepted_at = _stamp(now) if set_accepted else row["accepted_at"]
            ended_at = _stamp(now) if target_status in {"rejected", "ended"} else row["ended_at"]
            db.execute("""UPDATE lan_call_sessions
                SET status=?,accepted_at=?,ended_at=?
                WHERE session_hash=? AND status=?""",
                (target_status, accepted_at, ended_at, _session_digest(session_id), row["status"]))
            updated = db.execute("SELECT * FROM lan_call_sessions WHERE session_hash=?",
                                 (_session_digest(session_id),)).fetchone()
            self._append_audit(db, target_status, _session_digest(session_id),
                               device.owner_id, device.device_id)
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
            self._append_audit(db, status, _session_digest(session_id),
                               device.owner_id, device.device_id)
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
                self._append_audit(db, "expired", _session_digest(session_id),
                                   device.owner_id, device.device_id)
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
