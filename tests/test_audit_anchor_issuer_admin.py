import asyncio
import base64
import json
import tempfile

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core.audit_anchor_admin import AuditAnchorAdminASGI
from core.audit_anchor_credential_issuer import (
    AuditAnchorCredentialIssuer,
    AuditAnchorIssuanceError,
)
from core.audit_anchor_identity import AuditAnchorCredentialAuthenticator
from core.audit_anchor_service import SQLiteAuditAnchorService
from core.workload_identity import WorkloadSigningKey


class Signer:
    issuer = "anchor-issuer"

    def __init__(self):
        self.private = Ed25519PrivateKey.generate()
        self.public = self.private.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )

    def active_key(self, issuer):
        assert issuer == self.issuer
        return WorkloadSigningKey(issuer, "key-1", "EdDSA", self.public)

    def sign(self, issuer, key_id, message):
        assert (issuer, key_id) == (self.issuer, "key-1")
        return self.private.sign(message)


class Resolver:
    def __init__(self, signer):
        self.signer = signer

    def resolve(self, issuer, key_id, algorithm):
        if (issuer, key_id, algorithm) == ("anchor-issuer", "key-1", "EdDSA"):
            return self.signer.active_key(issuer)
        return None


class CredentialState:
    def __init__(self):
        self.used = set()
        self.revoked = set()

    def is_revoked(self, issuer, token_id):
        return (issuer, token_id) in self.revoked

    def consume_once(self, issuer, token_id, expires_at):
        pair = (issuer, token_id)
        if pair in self.used:
            return False
        self.used.add(pair)
        return True


class IdentitySource:
    def __init__(self, identity="publisher-1"):
        self.identity = identity

    def authenticate(self, handle):
        if handle != "trusted-bootstrap":
            raise PermissionError("invalid")
        return self.identity


def test_issuer_only_issues_to_enrolled_publishers_and_verifies():
    with tempfile.TemporaryDirectory() as directory:
        service = SQLiteAuditAnchorService(
            directory + "/anchor.db", authorize_administrator=lambda identity: identity == "admin"
        )
        signer = Signer()
        issuer = AuditAnchorCredentialIssuer(
            identity_source=IdentitySource(), publisher_registry=service,
            signer=signer, audience="https://anchor.test/v1", clock=lambda: 1000,
        )
        with pytest.raises(AuditAnchorIssuanceError, match="publisher_not_enrolled"):
            issuer.issue(bootstrap_handle="trusted-bootstrap", scopes=frozenset({"audit-anchor:read"}))
        service.set_publisher("admin", "publisher-1", enabled=True)
        token = issuer.issue(
            bootstrap_handle="trusted-bootstrap",
            scopes=frozenset({"audit-anchor:read", "audit-anchor:publish"}),
            ttl_seconds=90,
        )
        state = CredentialState()
        verifier = AuditAnchorCredentialAuthenticator(
            key_resolver=Resolver(signer), credential_state=state,
            audience="https://anchor.test/v1", clock=lambda: 1010,
        )
        assert verifier.verify(token, required_scope="audit-anchor:publish", now=1010) == "publisher-1"
        service.set_publisher("admin", "publisher-1", enabled=False)
        with pytest.raises(AuditAnchorIssuanceError, match="publisher_not_enrolled"):
            issuer.issue(bootstrap_handle="trusted-bootstrap", scopes=frozenset({"audit-anchor:read"}))


def test_issuer_rejects_scope_escalation_and_untrusted_bootstrap():
    with tempfile.TemporaryDirectory() as directory:
        service = SQLiteAuditAnchorService(
            directory + "/anchor.db", authorize_administrator=lambda identity: identity == "admin"
        )
        service.set_publisher("admin", "publisher-1", enabled=True)
        issuer = AuditAnchorCredentialIssuer(
            identity_source=IdentitySource(), publisher_registry=service,
            signer=Signer(), audience="https://anchor.test/v1", clock=lambda: 1000,
        )
        with pytest.raises(AuditAnchorIssuanceError, match="issuance_request_invalid"):
            issuer.issue(bootstrap_handle="trusted-bootstrap", scopes=frozenset({"audit-anchor:publishers:admin"}))
        with pytest.raises(AuditAnchorIssuanceError, match="publisher_authentication_failed"):
            issuer.issue(bootstrap_handle="attacker", scopes=frozenset({"audit-anchor:read"}))


async def request(app, method, path, identity):
    sent = []
    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}
    async def send(message):
        sent.append(message)
    await app({"type": "http", "method": method, "path": path, "headers": [],
               "trusted_admin": identity}, receive, send)
    return sent[0], sent[1]


def test_admin_api_separates_admin_identity_and_audits_enrollment_and_revocation():
    with tempfile.TemporaryDirectory() as directory:
        service = SQLiteAuditAnchorService(
            directory + "/anchor.db", authorize_administrator=lambda identity: identity == "admin-1"
        )
        app = AuditAnchorAdminASGI(service, lambda scope: scope.get("trusted_admin"))
        start, _ = asyncio.run(request(app, "PUT", "/v1/admin/publishers/publisher-1", "attacker"))
        assert start["status"] == 403
        start, _ = asyncio.run(request(app, "PUT", "/v1/admin/publishers/publisher-1", "admin-1"))
        assert start["status"] == 204
        assert service.is_publisher_active("publisher-1") is True
        start, body = asyncio.run(request(app, "GET", "/v1/admin/audit", "admin-1"))
        assert start["status"] == 200
        assert json.loads(body["body"])["events"][0]["operation"] == "enable"
        start, _ = asyncio.run(request(app, "DELETE", "/v1/admin/publishers/publisher-1", "admin-1"))
        assert start["status"] == 204
        assert service.is_publisher_active("publisher-1") is False
