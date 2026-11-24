from __future__ import annotations

import base64
import json

import pytest

from core.authorization import Principal
from core.workload_credential_issuer import (
    ActiveTaskLease,
    WorkloadCredentialIssuanceError,
    WorkloadCredentialIssuer,
)
from core.workload_identity import WorkloadSigningKey


class IdentitySource:
    def __init__(self, principal=None, error=None):
        self.principal = principal or Principal(
            "worker-1", "workload", "tenant-1", "agent-1",
            frozenset({"tool:read", "task:progress"}),
        )
        self.error = error

    def authenticate(self, handle):
        if self.error:
            raise self.error
        assert handle == "opaque-bootstrap-handle"
        return self.principal


class LeaseAuthority:
    def __init__(self, lease=None, error=None):
        self.lease = lease or ActiveTaskLease("task-1", "worker-1", "generation-7", 2_000)
        self.error = error

    def active_lease(self, task_id):
        if self.error:
            raise self.error
        assert task_id == "task-1"
        return self.lease


class Signer:
    issuer = "https://issuer.yuki.internal"

    def __init__(self, error=None, signature=b"s" * 64):
        self.error = error
        self.signature = signature
        self.calls = []

    def active_key(self, issuer):
        assert issuer == self.issuer
        return WorkloadSigningKey(issuer, "key-7", "EdDSA", b"p" * 32)

    def sign(self, issuer, key_id, message):
        if self.error:
            raise self.error
        self.calls.append((issuer, key_id, message))
        return self.signature


def make_issuer(identity=None, lease=None, signer=None):
    return WorkloadCredentialIssuer(
        identity_source=identity or IdentitySource(),
        lease_authority=lease or LeaseAuthority(),
        signer=signer or Signer(),
        clock=lambda: 1_000,
    )


def decode(segment):
    return json.loads(base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4)))


def test_issue_binds_identity_task_lease_and_attenuated_scopes():
    signer = Signer()
    token = make_issuer(signer=signer).issue(
        workload_handle="opaque-bootstrap-handle",
        task_id="task-1",
        audience="yuki-tool-runtime",
        scopes=frozenset({"tool:read"}),
        ttl_seconds=300,
        delegation_id="delegation-9",
    )
    header, claims, signature = token.split(".")
    assert decode(header) == {"alg": "EdDSA", "kid": "key-7", "typ": "yuki-workload+jwt"}
    payload = decode(claims)
    assert payload["sub"] == "worker-1"
    assert payload["tenant_id"] == "tenant-1"
    assert payload["agent_id"] == "agent-1"
    assert payload["task_id"] == "task-1"
    assert payload["lease_generation"] == "generation-7"
    assert payload["scope"] == ["tool:read"]
    assert payload["exp"] == 1_300
    assert payload["delegation_id"] == "delegation-9"
    assert len(base64.urlsafe_b64decode(signature + "=" * (-len(signature) % 4))) == 64
    assert signer.calls and signer.calls[0][2] == (header + "." + claims).encode("ascii")


def test_rejects_direct_or_non_workload_principal():
    human = Principal("human-1", "human", permissions=frozenset({"tool:read"}))
    with pytest.raises(WorkloadCredentialIssuanceError, match="workload_identity_or_scope_rejected"):
        make_issuer(identity=IdentitySource(principal=human)).issue(
            workload_handle="opaque-bootstrap-handle", task_id="task-1",
            audience="runtime", scopes=frozenset({"tool:read"}),
        )


def test_rejects_scope_escalation():
    with pytest.raises(WorkloadCredentialIssuanceError, match="workload_identity_or_scope_rejected"):
        make_issuer().issue(
            workload_handle="opaque-bootstrap-handle", task_id="task-1",
            audience="runtime", scopes=frozenset({"tool:admin"}),
        )


def test_rejects_wrong_worker_lease():
    lease = LeaseAuthority(ActiveTaskLease("task-1", "other-worker", "generation-7", 2_000))
    with pytest.raises(WorkloadCredentialIssuanceError, match="active_task_lease_required"):
        make_issuer(lease=lease).issue(
            workload_handle="opaque-bootstrap-handle", task_id="task-1",
            audience="runtime", scopes=frozenset({"tool:read"}),
        )


def test_rejects_expired_lease_and_caps_expiry_to_lease():
    expired = LeaseAuthority(ActiveTaskLease("task-1", "worker-1", "generation-7", 999))
    with pytest.raises(WorkloadCredentialIssuanceError, match="active_task_lease_required"):
        make_issuer(lease=expired).issue(
            workload_handle="opaque-bootstrap-handle", task_id="task-1",
            audience="runtime", scopes=frozenset({"tool:read"}),
        )
    short = LeaseAuthority(ActiveTaskLease("task-1", "worker-1", "generation-7", 1_050))
    token = make_issuer(lease=short).issue(
        workload_handle="opaque-bootstrap-handle", task_id="task-1",
        audience="runtime", scopes=frozenset({"tool:read"}), ttl_seconds=300,
    )
    assert decode(token.split(".")[1])["exp"] == 1_050


def test_identity_and_signer_failures_do_not_issue_credentials():
    with pytest.raises(WorkloadCredentialIssuanceError, match="workload_authentication_failed"):
        make_issuer(identity=IdentitySource(error=RuntimeError())).issue(
            workload_handle="opaque-bootstrap-handle", task_id="task-1",
            audience="runtime", scopes=frozenset({"tool:read"}),
        )
    signer = Signer(error=RuntimeError())
    with pytest.raises(WorkloadCredentialIssuanceError, match="workload_signing_failed"):
        make_issuer(signer=signer).issue(
            workload_handle="opaque-bootstrap-handle", task_id="task-1",
            audience="runtime", scopes=frozenset({"tool:read"}),
        )


def test_rejects_invalid_ttl_and_malformed_signature():
    with pytest.raises(WorkloadCredentialIssuanceError, match="issuance_request_invalid"):
        make_issuer().issue(
            workload_handle="opaque-bootstrap-handle", task_id="task-1",
            audience="runtime", scopes=frozenset({"tool:read"}), ttl_seconds=901,
        )
    signer = Signer(signature=b"short")
    with pytest.raises(WorkloadCredentialIssuanceError, match="workload_signature_invalid"):
        make_issuer(signer=signer).issue(
            workload_handle="opaque-bootstrap-handle", task_id="task-1",
            audience="runtime", scopes=frozenset({"tool:read"}),
        )


def test_rejects_signing_key_from_different_issuer():
    class MismatchedSigner(Signer):
        def active_key(self, issuer):
            return WorkloadSigningKey("https://attacker.invalid", "key-7", "EdDSA", b"p" * 32)

    with pytest.raises(WorkloadCredentialIssuanceError, match="workload_signing_key_invalid"):
        make_issuer(signer=MismatchedSigner()).issue(
            workload_handle="opaque-bootstrap-handle", task_id="task-1",
            audience="runtime", scopes=frozenset({"tool:read"}),
        )


def test_rejects_non_finite_clock():
    issuer = WorkloadCredentialIssuer(
        identity_source=IdentitySource(), lease_authority=LeaseAuthority(),
        signer=Signer(), clock=lambda: float("nan"),
    )
    with pytest.raises(WorkloadCredentialIssuanceError, match="issuer_clock_invalid"):
        issuer.issue(
            workload_handle="opaque-bootstrap-handle", task_id="task-1",
            audience="runtime", scopes=frozenset({"tool:read"}),
        )
