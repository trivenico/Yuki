"""End-to-end trust-boundary tests for the audit-anchor deployment path."""
import asyncio
import base64
import json
import tempfile
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core.audit_anchor_admin import AuditAnchorAdminASGI
from core.audit_anchor_credential_issuer import AuditAnchorCredentialIssuer, AuditAnchorIssuanceError
from core.audit_anchor_credential_state import SQLiteAuditAnchorCredentialState
from core.audit_anchor_identity import AuditAnchorCredentialAuthenticator, AuditAnchorCredentialError
from core.audit_anchor_service import AuditAnchorASGI, SQLiteAuditAnchorService
from core.workload_identity import WorkloadSigningKey


def _enc(value):
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


class RotatingSigner:
    issuer = "anchor-issuer"

    def __init__(self):
        self.rotate("key-a")

    def rotate(self, key_id):
        self.key_id = key_id
        self.private = Ed25519PrivateKey.generate()
        self.public = self.private.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )

    def active_key(self, issuer):
        assert issuer == self.issuer
        return WorkloadSigningKey(issuer, self.key_id, "EdDSA", self.public)

    def sign(self, issuer, key_id, message):
        assert (issuer, key_id) == (self.issuer, self.key_id)
        return self.private.sign(message)


class RotatingResolver:
    def __init__(self, signer):
        self.signer = signer
        self.trusted = {signer.key_id: signer.public}

    def rotate_trust(self):
        self.trusted = {self.signer.key_id: self.signer.public}

    def resolve(self, issuer, key_id, algorithm):
        public = self.trusted.get(key_id)
        if issuer != self.signer.issuer or algorithm != "EdDSA" or public is None:
            return None
        return WorkloadSigningKey(issuer, key_id, algorithm, public)


class BootstrapIdentity:
    def authenticate(self, handle):
        if handle != "host-authenticated-bootstrap":
            raise PermissionError("untrusted bootstrap")
        return "publisher-prod"


async def _request(app, method, path, *, headers=(), body=b"", extra_scope=None):
    messages = [{"type": "http.request", "body": body, "more_body": False}]
    sent = []

    async def receive():
        return messages.pop(0)

    async def send(message):
        sent.append(message)

    scope = {"type": "http", "method": method, "path": path, "headers": list(headers)}
    if extra_scope:
        scope.update(extra_scope)
    await app(scope, receive, send)
    return sent[0]["status"], sent[1].get("body", b""), dict(sent[0].get("headers", []))


def test_enrollment_issuance_publication_replay_revocation_and_key_rotation():
    with tempfile.TemporaryDirectory() as directory:
        signer = RotatingSigner()
        resolver = RotatingResolver(signer)
        service = SQLiteAuditAnchorService(
            str(Path(directory) / "anchor.sqlite3"),
            authorize_administrator=lambda identity: identity == "security-admin",
        )
        admin_app = AuditAnchorAdminASGI(
            service, lambda scope: scope.get("authenticated_admin")
        )
        publisher_app = AuditAnchorASGI(
            service,
            lambda scope: authenticator.authenticate_scope(scope),
        )
        state = SQLiteAuditAnchorCredentialState(
            str(Path(directory) / "credential-state.sqlite3"), clock=lambda: 1010
        )
        authenticator = AuditAnchorCredentialAuthenticator(
            key_resolver=resolver,
            credential_state=state,
            audience="https://anchor.example/v1",
            clock=lambda: 1010,
        )
        issuer = AuditAnchorCredentialIssuer(
            identity_source=BootstrapIdentity(),
            publisher_registry=service,
            signer=signer,
            audience="https://anchor.example/v1",
            clock=lambda: 1000,
        )

        # Enrollment is possible only through the separately authenticated admin app.
        status, _, _ = asyncio.run(_request(
            admin_app, "PUT", "/v1/admin/publishers/publisher-prod",
            extra_scope={"authenticated_admin": "security-admin"},
        ))
        assert status == 204
        assert service.is_publisher_active("publisher-prod") is True

        def issue():
            return issuer.issue(
                bootstrap_handle="host-authenticated-bootstrap",
                scopes=frozenset({"audit-anchor:read", "audit-anchor:publish"}),
                ttl_seconds=90,
            )

        def publisher_request(method, token, *, headers=(), body=b""):
            return asyncio.run(_request(
                publisher_app, method, "/v1/checkpoint",
                headers=[(b"authorization", ("Bearer " + token).encode()), *headers],
                body=body,
            ))

        # The issuer, durable replay state, verifier, and anchor service are connected.
        first_token = issue()
        status, _, response_headers = publisher_request(
            "PUT", first_token,
            headers=[(b"if-none-match", b"*")],
            body=json.dumps({"event_id": 1, "event_hash": "a" * 64}).encode(),
        )
        assert status == 200
        first_etag = response_headers[b"etag"]
        status, _, _ = publisher_request("GET", first_token)
        assert status == 401  # The same signed credential cannot be replayed.

        read_token = issue()
        status, body, _ = publisher_request("GET", read_token)
        assert status == 200
        assert json.loads(body)["event_id"] == 1

        # Rotation trusts only the new key; a still-unexpired old-key token is rejected.
        old_key_token = issue()
        signer.rotate("key-b")
        resolver.rotate_trust()
        with pytest.raises(AuditAnchorCredentialError, match="credential_key_untrusted"):
            authenticator.verify(old_key_token, required_scope="audit-anchor:read", now=1010)
        new_key_token = issue()
        status, _, _ = publisher_request(
            "PUT", new_key_token,
            headers=[(b"if-match", first_etag)],
            body=json.dumps({"event_id": 2, "event_hash": "b" * 64}).encode(),
        )
        assert status == 200

        # A token issued before revocation is blocked by the live publisher allowlist.
        outstanding_token = issue()
        status, _, _ = asyncio.run(_request(
            admin_app, "DELETE", "/v1/admin/publishers/publisher-prod",
            extra_scope={"authenticated_admin": "security-admin"},
        ))
        assert status == 204
        assert service.is_publisher_active("publisher-prod") is False
        with pytest.raises(AuditAnchorIssuanceError, match="publisher_not_enrolled"):
            issue()
        status, _, _ = publisher_request("GET", outstanding_token)
        assert status == 403

        # Admin audit captures both control-plane transitions.
        status, body, _ = asyncio.run(_request(
            admin_app, "GET", "/v1/admin/audit",
            extra_scope={"authenticated_admin": "security-admin"},
        ))
        assert status == 200
        operations = [event["operation"] for event in json.loads(body)["events"]]
        assert operations == ["enable", "revoke"]
