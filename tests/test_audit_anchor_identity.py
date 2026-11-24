import base64
import json
import unittest

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core.audit_anchor_identity import (
    AuditAnchorCredentialAuthenticator,
    AuditAnchorCredentialError,
)
from core.workload_identity import WorkloadSigningKey


def enc(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


class Resolver:
    def __init__(self, key):
        self.key = key

    def resolve(self, issuer, key_id, algorithm):
        if (issuer, key_id, algorithm) == (self.key.issuer, self.key.key_id, self.key.algorithm):
            return self.key
        return None


class State:
    def __init__(self):
        self.revoked = set()
        self.used = set()

    def is_revoked(self, issuer, token_id):
        return (issuer, token_id) in self.revoked

    def consume_once(self, issuer, token_id, expires_at):
        pair = (issuer, token_id)
        if pair in self.used:
            return False
        self.used.add(pair)
        return True


class AuditAnchorIdentityTests(unittest.TestCase):
    def setUp(self):
        self.private = Ed25519PrivateKey.generate()
        public = self.private.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        self.key = WorkloadSigningKey("anchor-issuer", "key-a", "EdDSA", public)
        self.state = State()
        self.auth = AuditAnchorCredentialAuthenticator(
            key_resolver=Resolver(self.key),
            credential_state=self.state,
            audience="https://anchor.example/v1",
            clock=lambda: 1100,
        )
        self.claims = {
            "iss": "anchor-issuer",
            "sub": "yuki-publisher-prod",
            "aud": "https://anchor.example/v1",
            "iat": 1000,
            "nbf": 1000,
            "exp": 1200,
            "jti": "credential-1",
            "scope": ["audit-anchor:read", "audit-anchor:publish"],
        }

    def token(self, claims=None, *, kid="key-a"):
        header = {"typ": "yuki-audit-anchor+jwt", "alg": "EdDSA", "kid": kid}
        payload = self.claims if claims is None else claims
        first = enc(json.dumps(header, separators=(",", ":")).encode())
        second = enc(json.dumps(payload, separators=(",", ":")).encode())
        signing_input = (first + "." + second).encode("ascii")
        return first + "." + second + "." + enc(self.private.sign(signing_input))

    def test_valid_scoped_token_returns_publisher_identity_once(self):
        self.assertEqual(self.auth.verify(self.token(), required_scope="audit-anchor:publish", now=1100), "yuki-publisher-prod")
        with self.assertRaisesRegex(AuditAnchorCredentialError, "credential_replayed"):
            self.auth.verify(self.token(), required_scope="audit-anchor:publish", now=1100)

    def test_wrong_audience_and_missing_scope_rejected(self):
        wrong = dict(self.claims, aud="https://other.example", jti="other")
        with self.assertRaisesRegex(AuditAnchorCredentialError, "credential_audience_mismatch"):
            self.auth.verify(self.token(wrong), required_scope="audit-anchor:publish", now=1100)
        read_only = dict(self.claims, scope=["audit-anchor:read"], jti="read-only")
        with self.assertRaisesRegex(AuditAnchorCredentialError, "credential_scope_insufficient"):
            self.auth.verify(self.token(read_only), required_scope="audit-anchor:publish", now=1100)

    def test_expired_and_overlong_credentials_rejected(self):
        expired = dict(self.claims, exp=1050, jti="expired")
        with self.assertRaisesRegex(AuditAnchorCredentialError, "credential_expired_or_not_yet_valid"):
            self.auth.verify(self.token(expired), required_scope="audit-anchor:read", now=1100)
        too_long = dict(self.claims, exp=1400, jti="long")
        with self.assertRaisesRegex(AuditAnchorCredentialError, "credential_time_invalid"):
            self.auth.verify(self.token(too_long), required_scope="audit-anchor:read", now=1100)

    def test_revoked_credential_rejected(self):
        self.state.revoked.add(("anchor-issuer", "credential-1"))
        with self.assertRaisesRegex(AuditAnchorCredentialError, "credential_revoked"):
            self.auth.verify(self.token(), required_scope="audit-anchor:read", now=1100)

    def test_retired_key_and_tampering_rejected(self):
        with self.assertRaisesRegex(AuditAnchorCredentialError, "credential_key_untrusted"):
            self.auth.verify(self.token(kid="retired-key"), required_scope="audit-anchor:read", now=1100)
        token = self.token()
        first, second, signature = token.split(".")
        altered = enc(json.dumps(dict(self.claims, sub="attacker"), separators=(",", ":")).encode())
        with self.assertRaisesRegex(AuditAnchorCredentialError, "credential_signature_invalid"):
            self.auth.verify(first + "." + altered + "." + signature, required_scope="audit-anchor:read", now=1100)

    def test_duplicate_json_and_duplicate_authorization_headers_rejected(self):
        header = enc(b'{"typ":"yuki-audit-anchor+jwt","alg":"EdDSA","kid":"key-a"}')
        payload = enc(b'{"iss":"anchor-issuer","iss":"attacker"}')
        signed = (header + "." + payload).encode("ascii")
        malformed = header + "." + payload + "." + enc(self.private.sign(signed))
        with self.assertRaisesRegex(AuditAnchorCredentialError, "credential_malformed"):
            self.auth.verify(malformed, required_scope="audit-anchor:read", now=1100)
        scope = {"method": "GET", "headers": [
            (b"authorization", ("Bearer " + self.token()).encode()),
            (b"authorization", ("Bearer " + self.token()).encode()),
        ]}
        self.assertIsNone(self.auth.authenticate_scope(scope))

    def test_asgi_scope_maps_methods_to_least_privilege(self):
        scope = {"method": "GET", "headers": [(b"authorization", ("Bearer " + self.token()).encode())]}
        self.assertEqual(self.auth.authenticate_scope(scope), "yuki-publisher-prod")
        scope = {"method": "DELETE", "headers": [(b"authorization", ("Bearer " + self.token()).encode())]}
        self.assertIsNone(self.auth.authenticate_scope(scope))


if __name__ == "__main__":
    unittest.main()
