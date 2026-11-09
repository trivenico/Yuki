"""Authenticated heartbeat and capability registry for paired Yuki devices.

This service accepts bounded metadata only. It does not accept shell commands,
code, model files, or arbitrary task payloads and does not execute remote work.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import json
import re
import sqlite3
from pathlib import Path
from typing import Any

from core.device_pairing import DevicePairingService


_NAME = re.compile(r"^[^\x00-\x1f\x7f]{1,80}$")
_STATUSES = frozenset({"idle", "busy", "degraded"})


def _now():
    return datetime.now(timezone.utc)


def _stamp(value):
    return value.astimezone(timezone.utc).isoformat()


@dataclass(frozen=True)
class DeviceHeartbeat:
    device_id: str
    status: str
    sequence: int
    reported_at: str
    last_seen_at: str
    capabilities: dict[str, Any]
    online: bool


class DeviceAgentService:
    """Record authenticated, replay-resistant heartbeat and hardware metadata."""

    def __init__(
        self,
        database_path: str | Path,
        pairing: DevicePairingService,
        *,
        online_window_seconds: int = 90,
        reservation_service=None,
    ):
        if not isinstance(pairing, DevicePairingService):
            raise TypeError("pairing must be a DevicePairingService")
        if isinstance(online_window_seconds, bool) or not isinstance(online_window_seconds, int) or not 15 <= online_window_seconds <= 3600:
            raise ValueError("online_window_seconds must be between 15 and 3600")
        self.database_path = str(Path(database_path).expanduser())
        if reservation_service is not None and not all(
            callable(getattr(reservation_service, method, None))
            for method in ("publish_capacity", "advance_capacity_sequence")
        ):
            raise TypeError("reservation_service_must_support_capacity_publication_and_watermarks")
        self.pairing = pairing
        self.online_window_seconds = online_window_seconds
        self.reservation_service = reservation_service
        Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("""
                CREATE TABLE IF NOT EXISTS device_agent_state (
                    device_id TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('idle','busy','degraded')),
                    sequence INTEGER NOT NULL CHECK(sequence >= 0),
                    reported_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    capabilities_json TEXT NOT NULL
                )
            """)
            db.execute("CREATE INDEX IF NOT EXISTS idx_device_agent_owner ON device_agent_state(owner_id,last_seen_at)")
            db.execute("""
                CREATE TABLE IF NOT EXISTS device_capacity_outbox (
                    device_id TEXT PRIMARY KEY, owner_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL, memory_bytes INTEGER NOT NULL,
                    cpu_millicores INTEGER NOT NULL, gpu_memory_bytes INTEGER NOT NULL
                )
            """)
            db.execute("""
                CREATE TABLE IF NOT EXISTS device_capacity_watermark_outbox (
                    device_id TEXT PRIMARY KEY, owner_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL
                )
            """)

    def _connect(self):
        return _Connection(sqlite3.connect(self.database_path, timeout=5.0))

    @staticmethod
    def _validate_capabilities(value: Any) -> dict[str, Any]:
        if not isinstance(value, dict) or set(value) - {
            "os", "architecture", "cpu_cores", "ram_total_bytes",
            "ram_available_bytes", "cpu_millicores", "gpus", "storage_free_bytes",
        }:
            raise ValueError("invalid_capabilities")
        result: dict[str, Any] = {}
        for key in ("os", "architecture"):
            item = value.get(key)
            if item is not None:
                if not isinstance(item, str) or not _NAME.fullmatch(item):
                    raise ValueError("invalid_capabilities")
                result[key] = item
        for key in ("cpu_cores", "cpu_millicores", "ram_total_bytes", "ram_available_bytes", "storage_free_bytes"):
            item = value.get(key)
            if item is not None:
                if type(item) is not int or item < 0 or item > 2**60:
                    raise ValueError("invalid_capabilities")
                result[key] = item
        if ("ram_available_bytes" in result and "ram_total_bytes" in result
                and result["ram_available_bytes"] > result["ram_total_bytes"]):
            raise ValueError("invalid_capabilities")
        if "cpu_cores" in result and result["cpu_cores"] > 4096:
            raise ValueError("invalid_capabilities")
        if "cpu_millicores" in result and result["cpu_millicores"] > 4_096_000:
            raise ValueError("invalid_capabilities")
        gpus = value.get("gpus", [])
        if not isinstance(gpus, list) or len(gpus) > 8:
            raise ValueError("invalid_capabilities")
        safe_gpus = []
        for gpu in gpus:
            if not isinstance(gpu, dict) or set(gpu) - {"name", "vendor", "memory_total_bytes", "memory_available_bytes"}:
                raise ValueError("invalid_capabilities")
            safe_gpu = {}
            for key in ("name", "vendor"):
                item = gpu.get(key)
                if item is not None:
                    if not isinstance(item, str) or not _NAME.fullmatch(item):
                        raise ValueError("invalid_capabilities")
                    safe_gpu[key] = item
            for key in ("memory_total_bytes", "memory_available_bytes"):
                item = gpu.get(key)
                if item is not None:
                    if type(item) is not int or item < 0 or item > 2**60:
                        raise ValueError("invalid_capabilities")
                    safe_gpu[key] = item
            if ("memory_available_bytes" in safe_gpu and "memory_total_bytes" in safe_gpu
                    and safe_gpu["memory_available_bytes"] > safe_gpu["memory_total_bytes"]):
                raise ValueError("invalid_capabilities")
            safe_gpus.append(safe_gpu)
        if sum(gpu.get("memory_available_bytes", 0) for gpu in safe_gpus) > 2**60:
            raise ValueError("invalid_capabilities")
        if "gpus" in value:
            result["gpus"] = safe_gpus
        if len(json.dumps(result, separators=(",", ":"))) > 12_000:
            raise ValueError("invalid_capabilities")
        return result

    def heartbeat(
        self,
        token: str,
        *,
        sequence: int,
        status: str,
        capabilities: dict[str, Any],
    ) -> DeviceHeartbeat:
        device = self.pairing.authenticate_device(token)
        if device is None:
            raise PermissionError("invalid_device_credential")
        if type(sequence) is not int or not 1 <= sequence <= 2**63 - 1:
            raise ValueError("invalid_sequence")
        if status not in _STATUSES:
            raise ValueError("invalid_status")
        safe_capabilities = self._validate_capabilities(capabilities)
        now = _now()
        timestamp = _stamp(now)
        capacity = self._capacity_from_capabilities(safe_capabilities)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT sequence FROM device_agent_state WHERE device_id=? AND owner_id=?",
                (device.device_id, device.owner_id),
            ).fetchone()
            if row is not None and sequence <= row["sequence"]:
                raise ValueError("stale_heartbeat")
            if capacity is not None:
                db.execute("""
                    INSERT INTO device_capacity_outbox(
                        device_id,owner_id,sequence,memory_bytes,cpu_millicores,gpu_memory_bytes
                    ) VALUES(?,?,?,?,?,?)
                    ON CONFLICT(device_id) DO UPDATE SET
                        owner_id=excluded.owner_id, sequence=excluded.sequence,
                        memory_bytes=excluded.memory_bytes,
                        cpu_millicores=excluded.cpu_millicores,
                        gpu_memory_bytes=excluded.gpu_memory_bytes
                """, (device.device_id, device.owner_id, sequence, *capacity))
                db.execute(
                    "DELETE FROM device_capacity_watermark_outbox WHERE device_id=? AND owner_id=?",
                    (device.device_id, device.owner_id),
                )
            else:
                # A newer incomplete heartbeat invalidates older pending capacity
                # and durably queues its sequence watermark in this same transaction.
                db.execute(
                    "DELETE FROM device_capacity_outbox WHERE device_id=? AND owner_id=?",
                    (device.device_id, device.owner_id),
                )
                db.execute("""
                    INSERT INTO device_capacity_watermark_outbox(device_id,owner_id,sequence)
                    VALUES(?,?,?)
                    ON CONFLICT(device_id) DO UPDATE SET
                        owner_id=excluded.owner_id, sequence=excluded.sequence
                """, (device.device_id, device.owner_id, sequence))
            db.execute("""
                INSERT INTO device_agent_state(
                    device_id,owner_id,status,sequence,reported_at,last_seen_at,capabilities_json
                ) VALUES(?,?,?,?,?,?,?)
                ON CONFLICT(device_id) DO UPDATE SET
                    status=excluded.status,
                    sequence=excluded.sequence,
                    reported_at=excluded.reported_at,
                    last_seen_at=excluded.last_seen_at,
                    capabilities_json=excluded.capabilities_json
                WHERE device_agent_state.owner_id=excluded.owner_id
            """, (device.device_id, device.owner_id, status, sequence, timestamp,
                  timestamp, json.dumps(safe_capabilities, separators=(",", ":"), sort_keys=True)))
        if self.reservation_service is not None:
            # Capacity publications and incomplete-report watermarks are both
            # delivered after the local heartbeat/outbox transaction commits.
            self.retry_pending_capacity(token)
        return DeviceHeartbeat(device.device_id, status, sequence, timestamp,
                               timestamp, safe_capabilities, True)


    @staticmethod
    def _capacity_from_capabilities(capabilities):
        memory = capabilities.get("ram_available_bytes")
        cpu = capabilities.get("cpu_millicores")
        if cpu is None and "cpu_cores" in capabilities:
            cpu = capabilities["cpu_cores"] * 1000
        # Absence of the GPU inventory is unknown, not proof of zero GPU
        # memory. An explicit empty inventory is a valid zero-GPU report.
        if "gpus" not in capabilities:
            return None
        gpus = capabilities["gpus"]
        if memory is None or cpu is None or not all(
            "memory_available_bytes" in gpu for gpu in gpus
        ):
            return None
        return memory, cpu, sum(gpu["memory_available_bytes"] for gpu in gpus)

    def retry_pending_capacity(self, token: str) -> bool:
        """Publish the newest durable heartbeat capacity snapshot for this paired device."""
        if self.reservation_service is None:
            return False
        device = self.pairing.authenticate_device(token)
        if device is None:
            raise PermissionError("invalid_device_credential")
        with self._connect() as db:
            capacity_row = db.execute("""
                SELECT sequence,memory_bytes,cpu_millicores,gpu_memory_bytes
                FROM device_capacity_outbox WHERE device_id=? AND owner_id=?
            """, (device.device_id, device.owner_id)).fetchone()
            watermark_row = db.execute("""
                SELECT sequence FROM device_capacity_watermark_outbox
                WHERE device_id=? AND owner_id=?
            """, (device.device_id, device.owner_id)).fetchone()
        if capacity_row is None and watermark_row is None:
            return False
        if watermark_row is not None and (
            capacity_row is None or watermark_row["sequence"] >= capacity_row["sequence"]
        ):
            sequence = watermark_row["sequence"]
            self.reservation_service.advance_capacity_sequence(
                token, source_sequence=sequence
            )
            with self._connect() as db:
                db.execute("""
                    DELETE FROM device_capacity_watermark_outbox
                    WHERE device_id=? AND owner_id=? AND sequence=?
                """, (device.device_id, device.owner_id, sequence))
            return True
        row = capacity_row
        self.reservation_service.publish_capacity(
            token, memory_bytes=row["memory_bytes"],
            cpu_millicores=row["cpu_millicores"],
            gpu_memory_bytes=row["gpu_memory_bytes"],
            source_sequence=row["sequence"],
        )
        with self._connect() as db:
            db.execute("""
                DELETE FROM device_capacity_outbox
                WHERE device_id=? AND owner_id=? AND sequence=?
            """, (device.device_id, device.owner_id, row["sequence"]))
        return True

    def list_devices(self, owner_id: str) -> list[DeviceHeartbeat]:
        if not isinstance(owner_id, str) or not owner_id.strip() or len(owner_id) > 128:
            raise ValueError("invalid_owner")
        now = _now()
        cutoff = _stamp(now - timedelta(seconds=self.online_window_seconds))
        with self._connect() as db:
            rows = db.execute("""
                SELECT device_id,status,sequence,reported_at,last_seen_at,capabilities_json
                FROM device_agent_state WHERE owner_id=? ORDER BY last_seen_at DESC LIMIT 100
            """, (owner_id,)).fetchall()
        return [
            DeviceHeartbeat(
                row["device_id"], row["status"], row["sequence"], row["reported_at"],
                row["last_seen_at"], json.loads(row["capabilities_json"]),
                row["last_seen_at"] >= cutoff,
            )
            for row in rows
        ]

    def revoke_device(self, owner_id: str, device_id: str) -> bool:
        """Revoke pairing credentials; stale registry metadata is retained for audit."""
        return self.pairing.revoke_device(owner_id, device_id)


def heartbeat_to_dict(heartbeat: DeviceHeartbeat) -> dict[str, Any]:
    return asdict(heartbeat)


class _Connection:
    def __init__(self, connection):
        self.connection = connection

    def __enter__(self):
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA busy_timeout=5000")
        self.connection.execute("PRAGMA foreign_keys=ON")
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
