"""Supervised, restart-safe revocation of credentials from expired task claims.

The durable task engine owns the recovery outbox. This coordinator performs
idempotent broker cleanup and acknowledges an outbox item only after the
worker-specific broker confirms the revocation operation.
"""
from __future__ import annotations

from typing import Any, Mapping


class LeaseRecoveryCoordinator:
    """Drain claim-revocation work using explicitly registered worker brokers."""

    def __init__(
        self,
        engine: Any,
        brokers_by_worker: Mapping[str, Any],
        *,
        supervisor_id: str,
    ) -> None:
        if not isinstance(supervisor_id, str) or not supervisor_id or len(supervisor_id) > 256:
            raise ValueError("supervisor_id must be non-empty bounded text")
        if not isinstance(brokers_by_worker, Mapping):
            raise TypeError("brokers_by_worker must be a worker-id mapping")
        normalized = {}
        for worker_id, broker in brokers_by_worker.items():
            if not isinstance(worker_id, str) or not worker_id or len(worker_id) > 256:
                raise ValueError("broker worker identity is invalid")
            if getattr(broker, "worker_id", None) != worker_id:
                raise ValueError("broker identity does not match its mapping key")
            if not callable(getattr(broker, "revoke_claim_digest", None)):
                raise TypeError("broker must provide revoke_claim_digest")
            normalized[worker_id] = broker
        for method in ("claim_recovery_outbox", "acknowledge_recovery_outbox", "release_recovery_outbox"):
            if not callable(getattr(engine, method, None)):
                raise TypeError(f"engine must provide {method}")
        self.engine = engine
        self.brokers_by_worker = normalized
        self.supervisor_id = supervisor_id

    def run_once(self, *, limit: int = 50, lease_seconds: int = 60) -> dict[str, int]:
        items = self.engine.claim_recovery_outbox(
            self.supervisor_id, limit=limit, lease_seconds=lease_seconds
        )
        revoked = acknowledged = deferred = 0
        for item in items:
            broker = self.brokers_by_worker.get(item["worker_id"])
            if broker is None:
                self.engine.release_recovery_outbox(item["id"], self.supervisor_id, item["claim_token"])
                deferred += 1
                continue
            try:
                # Revocation is idempotent. If the supervisor crashes after this
                # call but before ack, the next pass safely repeats it.
                revoked += broker.revoke_claim_digest(
                    item["task_id"], item["claim_token_sha256"]
                )
                if not self.engine.acknowledge_recovery_outbox(
                    item["id"], self.supervisor_id, item["claim_token"]
                ):
                    deferred += 1
                else:
                    acknowledged += 1
            except Exception:
                self.engine.release_recovery_outbox(item["id"], self.supervisor_id)
                deferred += 1
        return {
            "claimed": len(items),
            "revoked": revoked,
            "acknowledged": acknowledged,
            "deferred": deferred,
        }
