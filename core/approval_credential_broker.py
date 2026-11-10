"""Protected one-shot delivery for capability approval credentials.

This adapter deliberately does not persist secrets itself. Production composition
must supply a durable secret manager whose consume operation is atomic get-and-delete.
"""
from __future__ import annotations

from typing import Callable

from core.capability_approval_gateway import CapabilityApprovalGateway
from core.tool_policy import PolicyRequest


class ProtectedApprovalCredentialBroker:
    """Bridge approved request digests to an external protected secret store."""

    def __init__(
        self,
        *,
        put_secret: Callable[[str, str, int], None],
        consume_secret: Callable[[str], str | None],
        delete_secret: Callable[[str], None] | None = None,
    ) -> None:
        if not callable(put_secret) or not callable(consume_secret):
            raise TypeError("put_secret and consume_secret callbacks are required")
        if delete_secret is not None and not callable(delete_secret):
            raise TypeError("delete_secret must be callable")
        self.put_secret = put_secret
        self.consume_secret = consume_secret
        self.delete_secret = delete_secret

    @staticmethod
    def _key(request: PolicyRequest) -> str:
        return "yuki/approval/" + CapabilityApprovalGateway._request_digest(request)

    def deliver(
        self, request_id: str, request: PolicyRequest, token: str, ttl_seconds: int,
    ) -> None:
        if not isinstance(request_id, str) or len(request_id) != 32:
            raise ValueError("request_id is invalid")
        if not isinstance(request, PolicyRequest):
            raise TypeError("request must be a validated PolicyRequest")
        CapabilityApprovalGateway._token_digest(token)
        if type(ttl_seconds) is not int or not 1 <= ttl_seconds <= 86400:
            raise ValueError("credential lifetime is invalid")
        # The external store owns encryption, access controls, durability and TTL.
        # It must atomically replace an existing value for this exact request key.
        self.put_secret(self._key(request), token, ttl_seconds)

    def resolve(self, request: PolicyRequest) -> str | None:
        """Atomically retrieve and delete the credential for this exact request."""
        if not isinstance(request, PolicyRequest):
            raise TypeError("request must be a validated PolicyRequest")
        token = self.consume_secret(self._key(request))
        if token is None:
            return None
        CapabilityApprovalGateway._token_digest(token)
        return token

    def discard(self, request: PolicyRequest) -> None:
        """Best-effort cleanup for a request whose approval can no longer run."""
        if self.delete_secret is not None:
            self.delete_secret(self._key(request))
