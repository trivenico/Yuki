"""Central fail-closed authorization gateway with mandatory decision audit.

The gateway is the common policy boundary for task, tool, and future MCP adapters.
It does not treat a decision as a bearer token: resource-owning services must
call it immediately before their own atomic operation.
"""
from __future__ import annotations

from typing import Callable

from core.authorization_decision_receipts import AuthorizationDecisionEvidenceRecorder

from core.authorization import (
    AuthorizationDecision,
    AuthorizationPolicy,
    AuthorizationRequest,
    evaluate_authorization,
)


class AuthorizationGateway:
    """Evaluate typed policy and persist an audit record before returning a decision.

    The injected audit writer must durably persist the request's correlation
    metadata and decision. Audit failure converts even an allow into a deny.
    """

    def __init__(
        self,
        policy: AuthorizationPolicy,
        *,
        audit: Callable[[AuthorizationRequest, AuthorizationDecision], None],
        evidence_recorder: AuthorizationDecisionEvidenceRecorder | None = None,
    ) -> None:
        if not isinstance(policy, AuthorizationPolicy):
            raise TypeError("policy must implement AuthorizationPolicy")
        if not callable(audit):
            raise TypeError("a mandatory authorization audit writer is required")
        if evidence_recorder is not None and not isinstance(evidence_recorder, AuthorizationDecisionEvidenceRecorder):
            raise TypeError("independent_authorization_decision_evidence_recorder_required")
        self._policy = policy
        self._audit = audit
        self._evidence_recorder = evidence_recorder

    @property
    def audit_writer(self):
        """Expose the configured sink for trusted production composition checks."""
        return self._audit

    def authorize(self, request: AuthorizationRequest) -> AuthorizationDecision:
        if not isinstance(request, AuthorizationRequest):
            raise TypeError("request must be an AuthorizationRequest")
        decision = evaluate_authorization(self._policy, request)
        if self._evidence_recorder is not None:
            try:
                receipt_id = self._evidence_recorder.record_decision(request, decision)
                if not isinstance(receipt_id, str) or not receipt_id.strip():
                    raise RuntimeError("independent_decision_receipt_not_durably_recorded")
            except Exception:
                return AuthorizationDecision(
                    False, "authorization-evidence-unavailable",
                    decision.policy_revision, "evidence_unavailable",
                )
        try:
            audit_result = self._audit(request, decision)
            if audit_result is False:
                raise RuntimeError("authorization audit writer reported failure")
        except Exception:
            # Never execute an operation when its decision cannot be audited.
            return AuthorizationDecision(
                False,
                "authorization-audit-unavailable",
                decision.policy_revision,
                "audit_unavailable",
            )
        return decision

    def require(self, request: AuthorizationRequest) -> AuthorizationDecision:
        """Return only an audited allow; otherwise raise PermissionError."""
        decision = self.authorize(request)
        if decision.allowed is not True:
            raise PermissionError("authorization_denied:" + decision.reason_code)
        return decision
