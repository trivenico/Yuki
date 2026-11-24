import base64
import json
import unittest

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from core.workload_credential_verifier import (
    Ed25519WorkloadCredentialVerifier,
    WorkloadCredentialError,
)
from core.workload_identity import WorkloadSigningKey


def enc(raw):
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


class Resolver:
    def __init__(self, key):
        self.key = key
        self.calls = []

    def resolve(self, issuer, key_id, algorithm):
        self.calls.append((issuer, key_id, algorithm))
        if issuer == self.key.issuer and key_id == self.key.key_id and algorithm == self.key.algorithm:
            return self.key
        return None


class State:
    def __init__(self):
        self.revoked = set()
        self.consumed = set()
        self.fail = False

    def is_revoked(self, issuer, token_id):
        if self.fail:
            raise RuntimeError("backend down")
        return (issuer, token_id) in self.revoked

    def consume_once(self, issuer, token_id, expires_at):
        if self.fail:
            raise RuntimeError("backend down")
        key = (issuer, token_id)
        if key in self.consumed:
            return False
        self.consumed.add(key)
        return True


class Ed25519VerifierTests(unittest.TestCase):
    def setUp(self):
        self.private = Ed25519PrivateKey.generate()
        public = self.private.public_key().public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        self.key = WorkloadSigningKey(
            issuer="yuki-control-plane",
            key_id="key-2026-01",
            algorithm="EdDSA",
            public_key=public,
        )
        self.resolver = Resolver(self.key)
        self.state = State()
        self.verifier = Ed25519WorkloadCredentialVerifier(
            key_resolver=self.resolver,
            credential_state=self.state,
        )
        self.claims = {
            "iss": "yuki-control-plane",
            "sub": "workload:tenant-a:agent-1",
            "tenant_id": "tenant-a",
            "agent_id": "agent-1",
            "aud": "yuki-task-runtime",
            "iat": 1000,
            "nbf": 1000,
            "exp": 1300,
            "jti": "jti-unique-1",
            "task_id": "task-1",
            "lease_generation": "lease-generation-4",
            "scope": ["task:progress", "tool:invoke"],
        }

    def token(self, claims=None, header=None):
        header = header or {"typ": "yuki-workload+jwt", "alg": "EdDSA", "kid": "key-2026-01"}
        claims = self.claims if claims is None else claims
        first = enc(json.dumps(header, separators=(",", ":")).encode())
        second = enc(json.dumps(claims, separators=(",", ":")).encode())
        signing_input = (first + "." + second).encode("ascii")
        signature = self.private.sign(signing_input)
        return first + "." + second + "." + enc(signature)

    def verify(self, token=None, **kwargs):
        return self.verifier.verify(
            token or self.token(),
            expected_audience=kwargs.get("audience", "yuki-task-runtime"),
            expected_task_id=kwargs.get("task_id", "task-1"),
            expected_lease_generation=kwargs.get("lease", "lease-generation-4"),
            now=kwargs.get("now", 1100),
        )

    def test_valid_credential_verifies_and_consumes_once(self):
        claims = self.verify()
        self.assertEqual(claims.subject, self.claims["sub"])
        self.assertEqual(claims.scopes, frozenset(self.claims["scope"]))
        self.assertIn(("yuki-control-plane", "jti-unique-1"), self.state.consumed)

    def test_replay_is_rejected(self):
        token = self.token()
        self.verify(token)
        with self.assertRaisesRegex(WorkloadCredentialError, "credential_replayed"):
            self.verify(token)

    def test_revoked_credential_is_rejected(self):
        self.state.revoked.add(("yuki-control-plane", "jti-unique-1"))
        with self.assertRaisesRegex(WorkloadCredentialError, "credential_revoked"):
            self.verify()

    def test_revocation_backend_failure_fails_closed(self):
        self.state.fail = True
        with self.assertRaisesRegex(WorkloadCredentialError, "credential_state_unavailable"):
            self.verify()

    def test_tampered_payload_fails_signature(self):
        claims = dict(self.claims, tenant_id="tenant-b")
        with self.assertRaisesRegex(WorkloadCredentialError, "credential_signature_invalid"):
            self.verify(self.token(claims))

    def test_wrong_audience_task_and_lease_are_rejected(self):
        for kwargs, reason in [
            ({"audience": "other-service"}, "credential_audience_mismatch"),
            ({"task_id": "task-2"}, "credential_task_mismatch"),
            ({"lease": "lease-generation-5"}, "credential_lease_mismatch"),
        ]:
            with self.subTest(reason=reason):
                with self.assertRaisesRegex(WorkloadCredentialError, reason):
                    self.verify(**kwargs)

    def test_expired_and_future_credentials_are_rejected(self):
        with self.assertRaisesRegex(WorkloadCredentialError, "credential_expired_or_not_yet_valid"):
            self.verify(now=1400)
        future = dict(self.claims, iat=2000, nbf=2000, exp=2200, jti="future")
        with self.assertRaisesRegex(WorkloadCredentialError, "credential_expired_or_not_yet_valid"):
            self.verify(self.token(future), now=1100)

    def test_algorithm_confusion_is_rejected_before_key_lookup(self):
        header = {"typ": "yuki-workload+jwt", "alg": "HS256", "kid": "key-2026-01"}
        with self.assertRaisesRegex(WorkloadCredentialError, "credential_algorithm_rejected"):
            self.verify(self.token(header=header))
        self.assertEqual(self.resolver.calls, [])

    def test_unknown_key_is_rejected(self):
        header = {"typ": "yuki-workload+jwt", "alg": "EdDSA", "kid": "unknown"}
        with self.assertRaisesRegex(WorkloadCredentialError, "credential_key_untrusted"):
            self.verify(self.token(header=header))

    def test_duplicate_json_members_are_rejected(self):
        header = enc(b'{"typ":"yuki-workload+jwt","alg":"EdDSA","kid":"key-2026-01"}')
        payload = enc(b'{"iss":"yuki-control-plane","iss":"attacker"}')
        signing_input = (header + "." + payload).encode("ascii")
        token = header + "." + payload + "." + enc(self.private.sign(signing_input))
        with self.assertRaisesRegex(WorkloadCredentialError, "credential_malformed"):
            self.verify(token)

    def test_wrong_token_shape_is_rejected(self):
        for token in ("", "one.two", "a.b.c.d", "a..c"):
            with self.subTest(token=token):
                with self.assertRaises(WorkloadCredentialError):
                    self.verify(token)

    def test_boolean_timestamp_is_rejected(self):
        claims = dict(self.claims, iat=True, jti="boolean-time")
        with self.assertRaisesRegex(WorkloadCredentialError, "credential_time_invalid"):
            self.verify(self.token(claims))


if __name__ == "__main__":
    unittest.main()
