import base64
import json
import os
import tempfile
import unittest

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core.audit_anchor_identity import AuditAnchorCredentialAuthenticator, AuditAnchorCredentialError
from core.workload_credential_verifier import Ed25519WorkloadCredentialVerifier, WorkloadCredentialError
from core.workload_identity import WorkloadSigningKey
from core.workload_key_registry import SQLiteWorkloadKeyRegistry


def enc(value):
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


class State:
    def __init__(self):
        self.consumed = set()
    def is_revoked(self, issuer, token_id):
        return False
    def consume_once(self, issuer, token_id, expires_at):
        key = (issuer, token_id)
        if key in self.consumed:
            return False
        self.consumed.add(key)
        return True


class SharedKeyRegistryVerifierIntegration(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.registry = SQLiteWorkloadKeyRegistry(
            os.path.join(self.temp.name, "keys.sqlite"),
            authorize_admin=lambda name: name == "security-admin",
            clock=lambda: 1100,
        )
        self.private = Ed25519PrivateKey.generate()
        public = self.private.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        self.key = WorkloadSigningKey("yuki-issuer", "key-a", "EdDSA", public)
        self.registry.register("security-admin", self.key)
        self.registry.activate("security-admin", "yuki-issuer", "key-a")

    def tearDown(self):
        self.temp.cleanup()

    def sign(self, header, claims):
        h, p = enc(json.dumps(header, separators=(",", ":")).encode()), enc(
            json.dumps(claims, separators=(",", ":")).encode())
        signed = (h + "." + p).encode("ascii")
        return h + "." + p + "." + enc(self.private.sign(signed))

    def test_workload_verifier_uses_live_registry_and_revocation_is_immediate(self):
        state = State()
        verifier = Ed25519WorkloadCredentialVerifier(
            key_resolver=self.registry, credential_state=state)
        claims = {
            "iss":"yuki-issuer","sub":"worker:one","tenant_id":"tenant-a",
            "agent_id":"agent-a","aud":"task-runtime","iat":1000,"nbf":1000,
            "exp":1300,"jti":"task-token","task_id":"task-a",
            "lease_generation":"lease-1","scope":["task:progress"]}
        header = {"typ":"yuki-workload+jwt","alg":"EdDSA","kid":"key-a"}
        token = self.sign(header, claims)
        verifier.verify(token, expected_audience="task-runtime",
            expected_task_id="task-a", expected_lease_generation="lease-1", now=1100)
        self.registry.revoke("security-admin", "yuki-issuer", "key-a")
        claims["jti"] = "task-token-after-revoke"
        with self.assertRaisesRegex(WorkloadCredentialError, "credential_key_untrusted"):
            verifier.verify(self.sign(header, claims), expected_audience="task-runtime",
                expected_task_id="task-a", expected_lease_generation="lease-1", now=1100)

    def test_anchor_authenticator_uses_same_registry_and_live_rotation(self):
        state = State()
        authenticator = AuditAnchorCredentialAuthenticator(
            key_resolver=self.registry, credential_state=state,
            audience="audit-anchor", clock=lambda: 1100)
        claims = {"iss":"yuki-issuer","sub":"anchor-publisher","aud":"audit-anchor",
            "iat":1000,"nbf":1000,"exp":1200,"jti":"anchor-token",
            "scope":["audit-anchor:read"]}
        header = {"typ":"yuki-audit-anchor+jwt","alg":"EdDSA","kid":"key-a"}
        token = self.sign(header, claims)
        self.assertEqual(authenticator.verify(token, required_scope="audit-anchor:read"),
            "anchor-publisher")
        self.registry.revoke("security-admin", "yuki-issuer", "key-a")
        claims["jti"] = "anchor-token-after-revoke"
        with self.assertRaisesRegex(AuditAnchorCredentialError, "credential_key_untrusted"):
            authenticator.verify(self.sign(header, claims), required_scope="audit-anchor:read")

    def test_rotation_overlap_accepts_both_keys_then_retirement_rejects_old(self):
        replacement_private = Ed25519PrivateKey.generate()
        public = replacement_private.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        replacement = WorkloadSigningKey("yuki-issuer", "key-b", "EdDSA", public)
        self.registry.register("security-admin", replacement)
        self.registry.activate("security-admin", "yuki-issuer", "key-b")
        self.assertIsNotNone(self.registry.resolve("yuki-issuer", "key-a", "EdDSA"))
        self.assertIsNotNone(self.registry.resolve("yuki-issuer", "key-b", "EdDSA"))
        self.registry.retire("security-admin", "yuki-issuer", "key-a")
        self.assertIsNone(self.registry.resolve("yuki-issuer", "key-a", "EdDSA"))
        self.assertIsNotNone(self.registry.resolve("yuki-issuer", "key-b", "EdDSA"))


if __name__ == "__main__":
    unittest.main()
