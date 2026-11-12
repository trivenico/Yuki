"""Trusted router for approved credentials; worker lease secrets never leave workers."""
from __future__ import annotations

from typing import Any, Mapping

from core.approval_credential_broker import ApprovalCredentialBroker
from core.capability_approval_gateway import CapabilityApprovalGateway
from core.durable_task_engine import DurableTaskEngine


class ApprovalDeliveryRouter:
    """Route a minted token to the broker owning the exact live claim generation."""

    def __init__(
        self,
        engine: DurableTaskEngine,
        gateway: CapabilityApprovalGateway,
        brokers_by_worker: Mapping[str, ApprovalCredentialBroker],
    ) -> None:
        if not isinstance(engine, DurableTaskEngine):
            raise TypeError("engine must be a DurableTaskEngine")
        if not isinstance(gateway, CapabilityApprovalGateway):
            raise TypeError("gateway must be a CapabilityApprovalGateway")
        normalized = {}
        for worker_id, broker in brokers_by_worker.items():
            if not isinstance(broker, ApprovalCredentialBroker):
                raise TypeError("every delivery target must be an ApprovalCredentialBroker")
            if broker.worker_id != worker_id or broker.gateway is not gateway:
                raise ValueError("broker identity or gateway does not match router registration")
            if broker.validate_claim_digest is None:
                raise ValueError("broker must use authoritative digest-based claim validation")
            normalized[worker_id] = broker
        self.engine, self.gateway = engine, gateway
        self.brokers_by_worker = normalized

    def deliver(self, request_id: str, token: str) -> None:
        """Resolve request binding, then deliver only to its exact active worker."""
        metadata = self.gateway.approval_delivery_metadata(request_id)
        claim_digest = metadata.get("claim_token_sha256")
        active = self.engine.get_active_claim_by_digest(metadata["step_id"], claim_digest)
        if active is None:
            raise PermissionError("approval request no longer maps to an active claim")
        broker = self.brokers_by_worker.get(active["worker_id"])
        if broker is None:
            raise RuntimeError("no protected credential broker is registered for the active worker")
        broker.deliver_credential_by_digest(
            request_id, token, claim_token_sha256=claim_digest,
        )
