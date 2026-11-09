"""Authenticated, atomic resource leases for paired Yuki compute devices."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
from pathlib import Path
import re
import secrets
import sqlite3

from core.auth import BuiltInAuthService
from core.device_pairing import DevicePairingService

_ID = re.compile(r"^[A-Za-z0-9_-][A-Za-z0-9._:-]{0,127}$")
_MAX = 2**60


def _stamp(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("clock_must_return_timezone_aware_datetime")
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds")


def _amount(value: object) -> bool:
    return type(value) is int and 0 <= value <= _MAX


@dataclass(frozen=True)
class ResourceReservation:
    reservation_id: str
    owner_id: str
    device_id: str
    task_id: str
    memory_bytes: int
    cpu_millicores: int
    gpu_memory_bytes: int
    fencing_token: int
    expires_at: str
    status: str
    token: str = field(repr=False)


class ReservationError(RuntimeError):
    pass


class CapacityUnavailable(ReservationError):
    pass


class ReservationConflict(ReservationError):
    pass


class StaleReservation(ReservationError):
    pass


class DeviceResourceReservationService:
    """Issues leases only to authenticated users and publishes capacity from paired devices."""

    def __init__(self, database_path: str | Path, auth_service: BuiltInAuthService,
                 pairing_service: DevicePairingService, *, lease_seconds: int = 120,
                 capacity_max_age_seconds: int = 60, clock=None):
        if not isinstance(auth_service, BuiltInAuthService):
            raise TypeError("auth_service_must_be_BuiltInAuthService")
        if not isinstance(pairing_service, DevicePairingService):
            raise TypeError("pairing_service_must_be_DevicePairingService")
        if type(lease_seconds) is not int or not 5 <= lease_seconds <= 3600:
            raise ValueError("invalid_lease_seconds")
        if type(capacity_max_age_seconds) is not int or not 1 <= capacity_max_age_seconds <= 3600:
            raise ValueError("invalid_capacity_max_age_seconds")
        self.path = str(Path(database_path).expanduser())
        self.auth_service = auth_service
        self.pairing_service = pairing_service
        self.lease_seconds = lease_seconds
        self.capacity_max_age_seconds = capacity_max_age_seconds
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS resource_capacity(
                    owner_id TEXT NOT NULL, device_id TEXT NOT NULL,
                    memory_bytes INTEGER NOT NULL CHECK(memory_bytes>=0),
                    cpu_millicores INTEGER NOT NULL CHECK(cpu_millicores>=0),
                    gpu_memory_bytes INTEGER NOT NULL CHECK(gpu_memory_bytes>=0),
                    observed_at TEXT NOT NULL, generation INTEGER NOT NULL DEFAULT 1,
                    next_fence INTEGER NOT NULL DEFAULT 1,
                    PRIMARY KEY(owner_id,device_id));
                CREATE TABLE IF NOT EXISTS resource_reservations(
                    reservation_id TEXT PRIMARY KEY, owner_id TEXT NOT NULL,
                    device_id TEXT NOT NULL, task_id TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL, memory_bytes INTEGER NOT NULL,
                    cpu_millicores INTEGER NOT NULL, gpu_memory_bytes INTEGER NOT NULL,
                    fencing_token INTEGER NOT NULL, token_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL, expires_at TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('active','released','expired')),
                    UNIQUE(owner_id,idempotency_key));
                CREATE UNIQUE INDEX IF NOT EXISTS uq_active_resource_task
                    ON resource_reservations(owner_id,device_id,task_id) WHERE status='active';
                CREATE INDEX IF NOT EXISTS ix_active_resource_leases
                    ON resource_reservations(owner_id,device_id,status,expires_at);
            """)

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=10000")
        try:
            yield db
            if db.in_transaction:
                db.commit()
        except BaseException:
            if db.in_transaction:
                db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _id(value: object, label: str) -> str:
        if not isinstance(value, str) or not _ID.fullmatch(value):
            raise ValueError("invalid_" + label)
        return value

    def publish_capacity(self, device_token: str, *, memory_bytes: int,
                         cpu_millicores: int, gpu_memory_bytes: int = 0) -> int:
        """Publish capacity under the owner/device identity proven by a paired-device token."""
        device = self.pairing_service.authenticate_device(device_token)
        if device is None:
            raise PermissionError("authenticated_paired_device_required")
        for name, value in (("memory_bytes", memory_bytes),
                            ("cpu_millicores", cpu_millicores),
                            ("gpu_memory_bytes", gpu_memory_bytes)):
            if not _amount(value):
                raise ValueError("invalid_" + name)
        observed = _stamp(self.clock())
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("""
                INSERT INTO resource_capacity VALUES(?,?,?,?,?,?,1,1)
                ON CONFLICT(owner_id,device_id) DO UPDATE SET
                  memory_bytes=excluded.memory_bytes,
                  cpu_millicores=excluded.cpu_millicores,
                  gpu_memory_bytes=excluded.gpu_memory_bytes,
                  observed_at=excluded.observed_at,
                  generation=resource_capacity.generation+1
            """, (device.owner_id, device.device_id, memory_bytes,
                  cpu_millicores, gpu_memory_bytes, observed))
            row = db.execute(
                "SELECT generation FROM resource_capacity WHERE owner_id=? AND device_id=?",
                (device.owner_id, device.device_id)).fetchone()
            return int(row["generation"])

    def reserve(self, session_token: str, device_id: str, task_id: str, *,
                memory_bytes: int, cpu_millicores: int, gpu_memory_bytes: int = 0,
                idempotency_key: str, reservation_secret: str) -> ResourceReservation:
        """Reserve resources for the session owner; owner identity is never caller supplied."""
        principal = self.auth_service.resolve_session(session_token)
        if principal is None:
            raise PermissionError("authenticated_user_session_required")
        owner_id = principal.user_id
        device_id = self._id(device_id, "device_id")
        task_id = self._id(task_id, "task_id")
        idempotency_key = self._id(idempotency_key, "idempotency_key")
        for name, value in (("memory_bytes", memory_bytes),
                            ("cpu_millicores", cpu_millicores),
                            ("gpu_memory_bytes", gpu_memory_bytes)):
            if not _amount(value):
                raise ValueError("invalid_" + name)
        if (not isinstance(reservation_secret, str) or not 32 <= len(reservation_secret) <= 256
                or not reservation_secret.isascii()):
            raise ValueError("invalid_reservation_secret")
        requested = (memory_bytes, cpu_millicores, gpu_memory_bytes)
        now_dt = self.clock()
        now_s = _stamp(now_dt)
        now_dt = now_dt.astimezone(timezone.utc)
        expires = _stamp(now_dt + timedelta(seconds=self.lease_seconds))
        token_hash = hashlib.sha256(reservation_secret.encode("ascii")).hexdigest()
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("""UPDATE resource_reservations SET status='expired'
                WHERE owner_id=? AND device_id=? AND status='active' AND expires_at<=?""",
                (owner_id, device_id, now_s))
            old = db.execute("""SELECT * FROM resource_reservations
                WHERE owner_id=? AND idempotency_key=?""", (owner_id, idempotency_key)).fetchone()
            if old:
                same = (old["device_id"], old["task_id"], old["memory_bytes"],
                        old["cpu_millicores"], old["gpu_memory_bytes"]) == (
                        device_id, task_id, *requested)
                if not same:
                    raise ReservationConflict("idempotency_key_reused")
                if not hmac.compare_digest(old["token_hash"], token_hash):
                    raise ReservationConflict("reservation_secret_mismatch")
                if old["status"] != "active" or old["expires_at"] <= now_s:
                    raise StaleReservation("idempotent_reservation_is_no_longer_active")
                return ResourceReservation(
                    old["reservation_id"], old["owner_id"], old["device_id"], old["task_id"],
                    old["memory_bytes"], old["cpu_millicores"], old["gpu_memory_bytes"],
                    old["fencing_token"], old["expires_at"], old["status"], reservation_secret)
            cap = db.execute("""SELECT * FROM resource_capacity
                WHERE owner_id=? AND device_id=?""", (owner_id, device_id)).fetchone()
            if cap is None:
                raise CapacityUnavailable("capacity_not_published")
            observed = datetime.fromisoformat(cap["observed_at"])
            age = now_dt - observed.astimezone(timezone.utc)
            if age < timedelta(0) or age > timedelta(seconds=self.capacity_max_age_seconds):
                raise CapacityUnavailable("capacity_snapshot_stale")
            used = db.execute("""SELECT COALESCE(SUM(memory_bytes),0),
                COALESCE(SUM(cpu_millicores),0),COALESCE(SUM(gpu_memory_bytes),0)
                FROM resource_reservations WHERE owner_id=? AND device_id=?
                AND status='active' AND expires_at>?""",
                (owner_id, device_id, now_s)).fetchone()
            if any(used[i] + requested[i] > cap[key] for i, key in enumerate(
                    ("memory_bytes", "cpu_millicores", "gpu_memory_bytes"))):
                raise CapacityUnavailable("insufficient_unreserved_capacity")
            fence = int(cap["next_fence"])
            db.execute("""UPDATE resource_capacity SET next_fence=next_fence+1
                WHERE owner_id=? AND device_id=?""", (owner_id, device_id))
            rid = secrets.token_hex(16)
            try:
                db.execute("""INSERT INTO resource_reservations VALUES(
                    ?,?,?,?,?,?,?,?,?,?,?,?,'active')""",
                    (rid, owner_id, device_id, task_id, idempotency_key, *requested,
                     fence, token_hash, now_s, expires))
            except sqlite3.IntegrityError as exc:
                raise ReservationConflict("active_reservation_conflict") from exc
        return ResourceReservation(rid, owner_id, device_id, task_id, *requested,
                                   fence, expires, "active", reservation_secret)

    def renew(self, reservation_id: str, token: str, *, fencing_token: int) -> str:
        return self._transition(reservation_id, token, fencing_token, renew=True)

    def release(self, reservation_id: str, token: str, *, fencing_token: int) -> bool:
        try:
            self._transition(reservation_id, token, fencing_token, renew=False)
            return True
        except StaleReservation:
            return False

    def _transition(self, reservation_id: str, token: str, fencing_token: int, *, renew: bool):
        if not isinstance(reservation_id, str) or not re.fullmatch(r"[0-9a-f]{32}", reservation_id):
            raise ValueError("invalid_reservation_id")
        if (not isinstance(token, str) or not 32 <= len(token) <= 256 or not token.isascii()):
            raise ValueError("invalid_reservation_token")
        if type(fencing_token) is not int or fencing_token < 1:
            raise ValueError("invalid_fencing_token")
        now = _stamp(self.clock())
        token_hash = hashlib.sha256(token.encode("ascii")).hexdigest()
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            if renew:
                expiry = _stamp(datetime.fromisoformat(now) + timedelta(seconds=self.lease_seconds))
                sql = """UPDATE resource_reservations SET expires_at=? WHERE
                    reservation_id=? AND token_hash=? AND fencing_token=?
                    AND status='active' AND expires_at>?"""
                args = (expiry, reservation_id, token_hash, fencing_token, now)
            else:
                sql = """UPDATE resource_reservations SET status='released' WHERE
                    reservation_id=? AND token_hash=? AND fencing_token=?
                    AND status='active' AND expires_at>?"""
                args = (reservation_id, token_hash, fencing_token, now)
            if db.execute(sql, args).rowcount != 1:
                raise StaleReservation("reservation_expired_or_not_owned")
        return expiry if renew else None
