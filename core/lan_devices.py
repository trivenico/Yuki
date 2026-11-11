"""Owner-authorized local-network device control policy.

This module is a control-plane foundation, not a network scanner or remote
command executor. Devices must first be paired through DevicePairingService.
Endpoints are literal private/link-local IP addresses to avoid DNS rebinding.
Only host-defined capabilities can be granted; remote-advertised metadata is
never treated as authority.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
import ipaddress
import json
from pathlib import Path
import sqlite3
from urllib.parse import urlsplit


class LocalDeviceError(ValueError):
    """Invalid local-device registration or authorization request."""


class LocalDeviceDenied(PermissionError):
    """Device is unpaired, revoked, untrusted, or lacks an approved capability."""


class DeviceCapability(str, Enum):
    STATUS = "device.status"
    NOTIFY = "device.notify"
    MEDIA_PLAY_PAUSE = "media.play_pause"
    MEDIA_VOLUME = "media.volume"
    DISPLAY_BRIGHTNESS = "display.brightness"
    DEVICE_LOCK = "device.lock"


_CAPABILITIES = frozenset(item.value for item in DeviceCapability)
_OWNER_CONFIRMATION = frozenset({
    DeviceCapability.DEVICE_LOCK.value,
})
_MAX_ENDPOINT_LENGTH = 512


@dataclass(frozen=True)
class LocalDeviceEndpoint:
    device_id: str
    owner_id: str
    endpoint: str
    capabilities: tuple[str, ...]
    revision: int


@dataclass(frozen=True)
class LocalDeviceAuthorization:
    device_id: str
    owner_id: str
    capability: DeviceCapability
    endpoint: str
    policy_revision: int


def _validate_endpoint(value: str) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= _MAX_ENDPOINT_LENGTH:
        raise LocalDeviceError("endpoint_invalid")
    try:
        parsed = urlsplit(value)
        host = parsed.hostname
        port = parsed.port
        address = ipaddress.ip_address(host) if host else None
    except ValueError as exc:
        raise LocalDeviceError("endpoint_invalid") from exc
    if (address is None or address.version not in (4, 6)
            or parsed.scheme != "https"
            or parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.path not in {"", "/"} or "%" in (host or "")):
        raise LocalDeviceError("endpoint_invalid")
    # This service is intentionally for local-network peers, not arbitrary
    # internet hosts or DNS names. Literal IPs prevent DNS rebinding.
    if (address.is_loopback or address.is_global or address.is_multicast
            or address.is_unspecified or address.is_reserved
            or not (address.is_private or address.is_link_local)):
        raise LocalDeviceError("endpoint_must_be_local")
    normalized_port = port or 443
    if not 1 <= normalized_port <= 65535:
        raise LocalDeviceError("endpoint_port_invalid")
    host_text = f"[{address.compressed}]" if address.version == 6 else address.compressed
    return f"{parsed.scheme}://{host_text}:{normalized_port}"


class LocalNetworkDeviceRegistry:
    """Persistent owner-scoped endpoint/capability grants for paired LAN peers.

    The registry shares the pairing service's SQLite database. Authorization
    rechecks active pairing in the same transaction, so device revocation is
    effective for every subsequent command authorization.
    """

    def __init__(self, pairing_service):
        database_path = getattr(pairing_service, "database_path", None)
        if not isinstance(database_path, str) or not database_path:
            raise ValueError("pairing_service must expose its SQLite database_path")
        self.database_path = str(Path(database_path).expanduser())
        with self._connect() as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS lan_device_endpoints (
                    device_id TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    endpoint TEXT NOT NULL,
                    capabilities_json TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(device_id) REFERENCES paired_devices(device_id)
                )
            """)
        self._pairing_service = pairing_service

    def _connect(self):
        connection = sqlite3.connect(self.database_path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("PRAGMA foreign_keys=ON")
        return _SQLiteConnection(connection)

    @staticmethod
    def _identity(owner_id, device_id):
        if not isinstance(owner_id, str) or not 1 <= len(owner_id) <= 128 or not owner_id.strip():
            raise LocalDeviceError("owner_id_invalid")
        if not isinstance(device_id, str) or not 1 <= len(device_id) <= 128:
            raise LocalDeviceError("device_id_invalid")

    @staticmethod
    def _capabilities(values):
        if not isinstance(values, (list, tuple, set, frozenset)) or len(values) > len(_CAPABILITIES):
            raise LocalDeviceError("capabilities_invalid")
        normalized = []
        for value in values:
            if not isinstance(value, str) or value not in _CAPABILITIES:
                raise LocalDeviceError("capability_not_supported")
            if value not in normalized:
                normalized.append(value)
        return tuple(sorted(normalized))

    @staticmethod
    def _from_row(row):
        return LocalDeviceEndpoint(
            device_id=row["device_id"], owner_id=row["owner_id"],
            endpoint=row["endpoint"],
            capabilities=tuple(json.loads(row["capabilities_json"])),
            revision=row["revision"],
        )

    def register_endpoint(
        self, *, owner_id: str, device_id: str, endpoint: str,
        capabilities, owner_confirmed: bool, expected_revision: int | None = None,
    ) -> LocalDeviceEndpoint:
        """Register a paired peer's local address and explicit owner-approved grants."""
        self._identity(owner_id, device_id)
        if owner_confirmed is not True:
            raise LocalDeviceDenied("owner_confirmation_required")
        normalized_endpoint = _validate_endpoint(endpoint)
        normalized_capabilities = self._capabilities(capabilities)
        if expected_revision is not None and (type(expected_revision) is not int or expected_revision < 1):
            raise LocalDeviceError("expected_revision_invalid")
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            active = db.execute("""
                SELECT 1 FROM paired_devices
                WHERE owner_id=? AND device_id=? AND revoked_at IS NULL
            """, (owner_id, device_id)).fetchone()
            if active is None:
                raise LocalDeviceDenied("paired_device_required")
            existing = db.execute(
                "SELECT revision FROM lan_device_endpoints WHERE device_id=? AND owner_id=?",
                (device_id, owner_id),
            ).fetchone()
            if existing is None:
                if expected_revision is not None:
                    raise LocalDeviceError("endpoint_revision_conflict")
                revision = 1
                db.execute("""
                    INSERT INTO lan_device_endpoints
                    (device_id,owner_id,endpoint,capabilities_json,revision,updated_at)
                    VALUES(?,?,?,?,?,?)
                """, (device_id, owner_id, normalized_endpoint,
                      json.dumps(normalized_capabilities), revision, now))
            else:
                if type(expected_revision) is not int or expected_revision != existing["revision"]:
                    raise LocalDeviceError("endpoint_revision_conflict")
                revision = existing["revision"] + 1
                db.execute("""
                    UPDATE lan_device_endpoints
                    SET endpoint=?,capabilities_json=?,revision=?,updated_at=?
                    WHERE device_id=? AND owner_id=? AND revision=?
                """, (normalized_endpoint, json.dumps(normalized_capabilities),
                      revision, now, device_id, owner_id, existing["revision"]))
            row = db.execute(
                "SELECT * FROM lan_device_endpoints WHERE device_id=? AND owner_id=?",
                (device_id, owner_id),
            ).fetchone()
            return self._from_row(row)

    def list_devices(self, *, owner_id: str) -> tuple[LocalDeviceEndpoint, ...]:
        if not isinstance(owner_id, str) or not 1 <= len(owner_id) <= 128:
            raise LocalDeviceError("owner_id_invalid")
        with self._connect() as db:
            rows = db.execute("""
                SELECT e.* FROM lan_device_endpoints e
                JOIN paired_devices p ON p.device_id=e.device_id
                WHERE e.owner_id=? AND p.owner_id=? AND p.revoked_at IS NULL
                ORDER BY e.updated_at DESC LIMIT 100
            """, (owner_id, owner_id)).fetchall()
            return tuple(self._from_row(row) for row in rows)

    def authorize(
        self, *, owner_id: str, device_id: str, capability: DeviceCapability | str,
        owner_confirmed: bool = False,
    ) -> LocalDeviceAuthorization:
        """Authorize one typed capability; this method never sends a network command."""
        self._identity(owner_id, device_id)
        try:
            selected = capability if isinstance(capability, DeviceCapability) else DeviceCapability(capability)
        except (ValueError, TypeError) as exc:
            raise LocalDeviceDenied("capability_not_supported") from exc
        if type(owner_confirmed) is not bool:
            raise LocalDeviceDenied("confirmation_invalid")
        if selected.value in _OWNER_CONFIRMATION and owner_confirmed is not True:
            raise LocalDeviceDenied("owner_confirmation_required")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("""
                SELECT e.* FROM lan_device_endpoints e
                JOIN paired_devices p ON p.device_id=e.device_id
                WHERE e.owner_id=? AND e.device_id=? AND p.owner_id=?
                  AND p.revoked_at IS NULL
            """, (owner_id, device_id, owner_id)).fetchone()
            if row is None:
                raise LocalDeviceDenied("active_paired_device_required")
            grants = set(json.loads(row["capabilities_json"]))
            if selected.value not in grants:
                raise LocalDeviceDenied("capability_not_granted")
            return LocalDeviceAuthorization(
                device_id=device_id, owner_id=owner_id, capability=selected,
                endpoint=row["endpoint"], policy_revision=row["revision"],
            )

    def remove_endpoint(self, *, owner_id: str, device_id: str) -> bool:
        self._identity(owner_id, device_id)
        with self._connect() as db:
            changed = db.execute("""
                DELETE FROM lan_device_endpoints WHERE owner_id=? AND device_id=?
            """, (owner_id, device_id)).rowcount
            return changed == 1


class _SQLiteConnection:
    """Commit/rollback and always close a registry connection (including on Windows)."""

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
