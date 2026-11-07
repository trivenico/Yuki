"""Host-controlled reviewer signing-key registry.

This registry keeps key material in host memory supplied by protected storage.
Reviewer workers receive neither key bytes nor signer objects. The ledger calls
the resolver inside the trusted host process for signing and verification.
"""
from __future__ import annotations

from dataclasses import dataclass
from threading import RLock
from typing import Callable, Mapping

from automation.reviewer_signing import HMACReviewerSigner


class ReviewerKeyRegistryError(RuntimeError):
    pass


@dataclass(frozen=True)
class _KeyRecord:
    reviewer_agent_id: str
    signer: HMACReviewerSigner
    active_for_signing: bool
    revoked: bool


class HostReviewerSigningKeyRegistry:
    """Resolve active and historical signers under host-side authorization.

    authorize(reviewer_id, action) must return literal True. Key bytes should
    be loaded by the host from an OS credential store/KMS, never from an agent.
    """

    def __init__(self, *, authorize: Callable[[str, str], bool]):
        if not callable(authorize):
            raise TypeError("trusted_signing_authorizer_required")
        self._authorize = authorize
        self._records: dict[tuple[str, str], _KeyRecord] = {}
        self._lock = RLock()

    def register(self, *, reviewer_agent_id: str, signer: HMACReviewerSigner,
                 active_for_signing: bool = False) -> None:
        if not isinstance(reviewer_agent_id, str) or not reviewer_agent_id.strip():
            raise ValueError("reviewer_identity_required")
        if not isinstance(signer, HMACReviewerSigner):
            raise TypeError("host_hmac_signer_required")
        if type(active_for_signing) is not bool:
            raise ValueError("active_for_signing_must_be_boolean")
        key = (reviewer_agent_id, signer.key_id)
        with self._lock:
            if key in self._records:
                raise ReviewerKeyRegistryError("reviewer_key_id_already_registered")
            if active_for_signing:
                for existing_key, record in tuple(self._records.items()):
                    if existing_key[0] == reviewer_agent_id and record.active_for_signing:
                        self._records[existing_key] = _KeyRecord(
                            record.reviewer_agent_id, record.signer, False, record.revoked
                        )
            self._records[key] = _KeyRecord(
                reviewer_agent_id, signer, active_for_signing, False
            )

    def activate(self, *, reviewer_agent_id: str, key_id: str) -> None:
        with self._lock:
            record = self._records.get((reviewer_agent_id, key_id))
            if record is None or record.revoked:
                raise ReviewerKeyRegistryError("reviewer_signing_key_unavailable")
            for key, existing in tuple(self._records.items()):
                if key[0] == reviewer_agent_id and existing.active_for_signing:
                    self._records[key] = _KeyRecord(
                        existing.reviewer_agent_id, existing.signer, False, existing.revoked
                    )
            self._records[(reviewer_agent_id, key_id)] = _KeyRecord(
                record.reviewer_agent_id, record.signer, True, False
            )

    def revoke(self, *, reviewer_agent_id: str, key_id: str) -> None:
        with self._lock:
            record = self._records.get((reviewer_agent_id, key_id))
            if record is None:
                raise ReviewerKeyRegistryError("reviewer_signing_key_unavailable")
            self._records[(reviewer_agent_id, key_id)] = _KeyRecord(
                record.reviewer_agent_id, record.signer, False, True
            )

    def resolve(self, reviewer_agent_id: str, key_id: str | None) -> HMACReviewerSigner | None:
        if not isinstance(reviewer_agent_id, str) or not reviewer_agent_id.strip():
            return None
        action = "review:sign" if key_id is None else "review:verify_signature"
        try:
            if self._authorize(reviewer_agent_id, action) is not True:
                return None
        except Exception:
            return None
        with self._lock:
            if key_id is None:
                matches = [
                    record for (agent, _), record in self._records.items()
                    if agent == reviewer_agent_id and record.active_for_signing and not record.revoked
                ]
                return matches[0].signer if len(matches) == 1 else None
            record = self._records.get((reviewer_agent_id, key_id))
            return record.signer if record is not None and not record.revoked else None


__all__ = ["HostReviewerSigningKeyRegistry", "ReviewerKeyRegistryError"]
