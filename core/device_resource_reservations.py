"""SQLite-backed atomic resource reservations for paired inference devices."""
from __future__ import annotations

from dataclasses import dataclass, field
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import re
import sqlite3
from pathlib import Path
from typing import Callable

_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
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
    """Capacity snapshots plus atomic leases; never dispatches work itself."""

    def __init__(self, database_path: str | Path, *, lease_seconds: int = 120,
                 capacity_max_age_seconds: int = 60,
                 clock: Callable[[], datetime] | None = None):
        if type(lease_seconds) is not int or not 5 <= lease_seconds <= 3600:
            raise ValueError("invalid_lease_seconds")
        if type(capacity_max_age_seconds) is not int or not 1 <= capacity_max_age_seconds <= 3600:
            raise ValueError("invalid_capacity_max_age_seconds")
        self.path = str(Path(database_path).expanduser())
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

    def publish_capacity(self, owner_id: str, device_id: str, *,
                         memory_bytes: int, cpu_millicores: int,
                         gpu_memory_bytes: int = 0) -> int:
        owner_id = self._id(owner_id, "owner_id")
        device_id = self._id(device_id, "device_id")
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
            """, (owner_id,device_id,memory_bytes,cpu_millicores,gpu_memory_bytes,observed))
            return int(db.execute(
                "SELECT generation FROM resource_capacity WHERE owner_id=? AND device_id=?",
                (owner_id,device_id)).fetchone()[0])

    def reserve(self, owner_id: str, device_id: str, task_id: str, *,
                memory_bytes: int, cpu_millicores: int, gpu_memory_bytes: int = 0,
                idempotency_key: str, reservation_secret: str) -> ResourceReservation:
        owner_id = self._id(owner_id, "owner_id")
        device_id = self._id(device_id, "device_id")
        task_id = self._id(task_id, "task_id")
        idempotency_key = self._id(idempotency_key, "idempotency_key")
        for name, value in (("memory_bytes", memory_bytes),
                            ("cpu_millicores", cpu_millicores),
                            ("gpu_memory_bytes", gpu_memory_bytes)):
            if not _amount(value):
                raise ValueError("invalid_" + name)
        requested = (memory_bytes,cpu_millicores,gpu_memory_bytes)
        if (not isinstance(reservation_secret, str)
                or len(reservation_secret) < 32
                or len(reservation_secret) > 256
                or not reservation_secret.isascii()):
            raise ValueError("invalid_reservation_secret")
        now_dt = self.clock()
        now_s = _stamp(now_dt)
        now_dt = now_dt.astimezone(timezone.utc)
        expires = _stamp(now_dt + timedelta(seconds=self.lease_seconds))
        token = reservation_secret
        token_hash = hashlib.sha256(token.encode("ascii")).hexdigest()
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("""UPDATE resource_reservations SET status='expired'
                WHERE owner_id=? AND device_id=? AND status='active' AND expires_at<=?""",
                (owner_id,device_id,now_s))
            old = db.execute("SELECT * FROM resource_reservations WHERE owner_id=? AND idempotency_key=?",
                             (owner_id,idempotency_key)).fetchone()
            if old:
                same = (old["device_id"],old["task_id"],old["memory_bytes"],
                        old["cpu_millicores"],old["gpu_memory_bytes"]) == (
                        device_id,task_id,*requested)
                if not same:
                    raise ReservationConflict("idempotency_key_reused")
                if not hmac.compare_digest(old["token_hash"], token_hash):
                    raise ReservationConflict("reservation_secret_mismatch")
                if old["status"] != "active" or old["expires_at"] <= now_s:
                    raise StaleReservation("idempotent_reservation_is_no_longer_active")
                return ResourceReservation(
                    old["reservation_id"], old["owner_id"], old["device_id"],
                    old["task_id"], old["memory_bytes"], old["cpu_millicores"],
                    old["gpu_memory_bytes"], old["fencing_token"], old["expires_at"],
                    old["status"], token,
                )
            cap = db.execute("SELECT * FROM resource_capacity WHERE owner_id=? AND device_id=?",
                             (owner_id,device_id)).fetchone()
            if cap is None:
                raise CapacityUnavailable("capacity_not_published")
            observed = datetime.fromisoformat(cap["observed_at"])
            if now_dt - observed.astimezone(timezone.utc) > timedelta(seconds=self.capacity_max_age_seconds):
                raise CapacityUnavailable("capacity_snapshot_stale")
            used = db.execute("""SELECT COALESCE(SUM(memory_bytes),0),
                COALESCE(SUM(cpu_millicores),0),COALESCE(SUM(gpu_memory_bytes),0)
                FROM resource_reservations WHERE owner_id=? AND device_id=?
                AND status='active' AND expires_at>?""", (owner_id,device_id,now_s)).fetchone()
            if any(used[i] + requested[i] > cap[k] for i,k in enumerate(
                    ("memory_bytes","cpu_millicores","gpu_memory_bytes"))):
                raise CapacityUnavailable("insufficient_unreserved_capacity")
            fence = int(cap["next_fence"])
            db.execute("UPDATE resource_capacity SET next_fence=next_fence+1 WHERE owner_id=? AND device_id=?",
                       (owner_id,device_id))
            rid = secrets.token_hex(16)
            try:
                db.execute("""INSERT INTO resource_reservations VALUES(?,?,?,?,?,?,?,?,?,?,?,?,'active')""",
                    (rid,owner_id,device_id,task_id,idempotency_key,*requested,fence,token_hash,now_s,expires))
            except sqlite3.IntegrityError as exc:
                raise ReservationConflict("active_reservation_conflict") from exc
        return ResourceReservation(rid,owner_id,device_id,task_id,*requested,fence,expires,"active",token)

    def renew(self, reservation_id: str, token: str, *, fencing_token: int) -> str:
        return self._transition(reservation_id,token,fencing_token,renew=True)

    def release(self, reservation_id: str, token: str, *, fencing_token: int) -> bool:
        try:
            self._transition(reservation_id,token,fencing_token,renew=False)
            return True
        except StaleReservation:
            return False

    def _transition(self, reservation_id: str, token: str, fencing_token: int, *, renew: bool):
        if not isinstance(reservation_id,str) or not re.fullmatch(r"[0-9a-f]{32}",reservation_id):
            raise ValueError("invalid_reservation_id")
        if not isinstance(token,str) or not 20 <= len(token) <= 256:
            raise ValueError("invalid_reservation_token")
        if type(fencing_token) is not int or fencing_token < 1:
            raise ValueError("invalid_fencing_token")
        now = _stamp(self.clock())
        token_hash = hashlib.sha256(token.encode("ascii",errors="ignore")).hexdigest()
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            if renew:
                expiry = _stamp(datetime.fromisoformat(now) + timedelta(seconds=self.lease_seconds))
                sql = """UPDATE resource_reservations SET expires_at=? WHERE
                    reservation_id=? AND token_hash=? AND fencing_token=?
                    AND status='active' AND expires_at>?"""
                args = (expiry,reservation_id,token_hash,fencing_token,now)
            else:
                sql = """UPDATE resource_reservations SET status='released' WHERE
                    reservation_id=? AND token_hash=? AND fencing_token=?
                    AND status='active' AND expires_at>?"""
                args = (reservation_id,token_hash,fencing_token,now)
            changed = db.execute(sql,args).rowcount
            if changed != 1:
                raise StaleReservation("reservation_expired_or_not_owned")
        return expiry if renew else None
