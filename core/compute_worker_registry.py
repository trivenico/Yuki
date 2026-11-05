"""Owner-scoped admission of compute-worker capabilities from paired devices.

The registry trusts device identity only after DevicePairingService validates
the bearer credential. Capability records are process-local and are never
persisted as authentication evidence. This is not a network server.
"""
from __future__ import annotations

from dataclasses import replace
from threading import RLock
from typing import Any

from core.compute_worker_protocol import (
    ComputeWorkerCapabilities,
    ComputeWorkerProtocolError,
)
from core.device_pairing import DevicePairingService


class ComputeWorkerRegistrationError(PermissionError):
    """A worker could not be admitted to the owner's compute pool."""


class PairedComputeWorkerRegistry:
    """In-memory capability registry backed by owner-bound device credentials."""

    def __init__(self, pairing: DevicePairingService, *, max_workers_per_owner: int = 32):
        if not isinstance(pairing, DevicePairingService):
            raise TypeError("pairing must be a DevicePairingService")
        if type(max_workers_per_owner) is not int or not 1 <= max_workers_per_owner <= 256:
            raise ValueError("max_workers_per_owner must be between 1 and 256")
        self._pairing = pairing
        self._max_workers = max_workers_per_owner
        self._lock = RLock()
        self._records: dict[str, dict[str, ComputeWorkerCapabilities]] = {}
        self._enabled: dict[tuple[str, str], bool] = {}

    @staticmethod
    def _owner(owner_id: str) -> str:
        if not isinstance(owner_id, str) or not owner_id.strip() or len(owner_id) > 128:
            raise ComputeWorkerRegistrationError("invalid_owner")
        return owner_id

    def register(
        self,
        *,
        owner_id: str,
        credential: str,
        capabilities: ComputeWorkerCapabilities,
        now_epoch: int,
    ) -> ComputeWorkerCapabilities:
        owner = self._owner(owner_id)
        if not isinstance(capabilities, ComputeWorkerCapabilities):
            raise ComputeWorkerProtocolError("invalid_worker_capabilities")
        if type(now_epoch) is not int or now_epoch < 0:
            raise ComputeWorkerProtocolError("invalid_now_epoch")
        device = self._pairing.authenticate_device(credential)
        if device is None or device.owner_id != owner:
            raise ComputeWorkerRegistrationError("device_not_authorized")
        if capabilities.worker_id != device.device_id:
            raise ComputeWorkerRegistrationError("worker_identity_mismatch")

        key = (owner, device.device_id)
        with self._lock:
            owner_records = self._records.setdefault(owner, {})
            if device.device_id not in owner_records and len(owner_records) >= self._max_workers:
                raise ComputeWorkerRegistrationError("owner_worker_limit_exceeded")
            enabled = self._enabled.get(key, True)
            # Pairing is established by the credential, not by the untrusted
            # boolean in the advertisement. Enablement is owner-controlled.
            admitted = replace(
                capabilities, paired=True, enabled=enabled, last_seen_epoch=now_epoch
            )
            owner_records[device.device_id] = admitted
            return admitted

    def set_enabled(self, *, owner_id: str, worker_id: str, enabled: bool) -> bool:
        owner = self._owner(owner_id)
        if not isinstance(worker_id, str) or not worker_id:
            raise ValueError("invalid_worker_id")
        if type(enabled) is not bool:
            raise ValueError("enabled must be a boolean")
        key = (owner, worker_id)
        with self._lock:
            record = self._records.get(owner, {}).get(worker_id)
            if record is None:
                return False
            self._enabled[key] = enabled
            self._records[owner][worker_id] = replace(record, enabled=enabled)
            return True

    def scheduler_records(
        self, *, owner_id: str, now_epoch: int, max_age_seconds: int = 90
    ) -> list[dict[str, Any]]:
        owner = self._owner(owner_id)
        if type(now_epoch) is not int or now_epoch < 0:
            raise ValueError("invalid_now_epoch")
        with self._lock:
            records = tuple(self._records.get(owner, {}).values())
        return [
            record.to_scheduler_record(now_epoch=now_epoch, max_age_seconds=max_age_seconds)
            for record in sorted(records, key=lambda item: item.worker_id)
        ]

    def revoke_from_registry(self, *, owner_id: str, worker_id: str) -> bool:
        owner = self._owner(owner_id)
        key = (owner, worker_id)
        with self._lock:
            removed = self._records.get(owner, {}).pop(worker_id, None)
            self._enabled.pop(key, None)
            return removed is not None
