import unittest

from core.authorization import Principal
from core.workload_identity import (
    OAuthWorkloadIdentityVerifier,
    WorkloadIdentityError,
)


class FakeIntrospectionTransport:
    def __init__(self, claims):
        self.claims = claims
        self.calls = []

    def request_form(self, endpoint, *, fields, headers=None):
        self.calls.append((endpoint, fields, headers))
        if isinstance(self.claims, Exception):
            raise self.claims
        return self.claims


class WorkloadIdentityVerifierTests(unittest.TestCase):
    def setUp(self):
        self.now = 1_800_000_000
        self.claims = {
            "active": True,
            "iss": "https://identity.example.test",
            "client_id": "yuki-workers",
            "sub": "worker-subject-1",
            "tenant_id": "tenant-1",
            "agent_id": "research-agent",
            "jti": "credential-1",
            "aud": ["yuki-task-runtime"],
            "iat": self.now - 30,
            "exp": self.now + 300,
            "scope": "task:read task:progress tool:invoke",
            "token_type": "Bearer",
            "task_id": "task-123",
        }
        self.transport = FakeIntrospectionTransport(self.claims)
        self.verifier = OAuthWorkloadIdentityVerifier(
            transport=self.transport,
            introspection_endpoint="https://identity.example.test/introspect",
            issuer="https://identity.example.test",
            audience="yuki-task-runtime",
            client_id="yuki-workers",
            introspection_headers={"Authorization": "Basic host-managed-secret"},
            clock=lambda: self.now,
        )

    def test_resolves_verified_workload_principal(self):
        result = self.verifier.verify(
            access_token="opaque-access-token", expected_task_id="task-123"
        )
        self.assertIsInstance(result.principal, Principal)
        self.assertEqual(result.principal.kind, "workload")
        self.assertEqual(result.principal.principal_id, "worker-subject-1")
        self.assertEqual(result.principal.tenant_id, "tenant-1")
        self.assertEqual(result.principal.agent_id, "research-agent")
        self.assertEqual(result.principal.permissions, frozenset({
            "task:read", "task:progress", "tool:invoke",
        }))
        self.assertEqual(self.transport.calls[0][1]["token"], "opaque-access-token")

    def test_rejects_inactive_or_revoked_credentials(self):
        self.claims["active"] = False
        with self.assertRaises(WorkloadIdentityError):
            self.verifier.verify(access_token="opaque-access-token")

    def test_rejects_issuer_client_and_audience_mismatch(self):
        for key, value in (
            ("iss", "https://attacker.example"),
            ("client_id", "other-client"),
            ("aud", ["other-service"]),
        ):
            claims = dict(self.claims)
            claims[key] = value
            verifier = OAuthWorkloadIdentityVerifier(
                transport=FakeIntrospectionTransport(claims),
                introspection_endpoint="https://identity.example.test/introspect",
                issuer="https://identity.example.test",
                audience="yuki-task-runtime",
                client_id="yuki-workers",
                introspection_headers={"Authorization": "Basic host-managed-secret"},
                clock=lambda: self.now,
            )
            with self.subTest(key=key), self.assertRaises(WorkloadIdentityError):
                verifier.verify(access_token="opaque-access-token")

    def test_rejects_expired_future_and_overlong_tokens(self):
        for updates in (
            {"exp": self.now - 100},
            {"iat": self.now + 100},
            {"exp": self.now + 5000},
        ):
            claims = dict(self.claims)
            claims.update(updates)
            verifier = OAuthWorkloadIdentityVerifier(
                transport=FakeIntrospectionTransport(claims),
                introspection_endpoint="https://identity.example.test/introspect",
                issuer="https://identity.example.test",
                audience="yuki-task-runtime",
                client_id="yuki-workers",
                introspection_headers={"Authorization": "Basic host-managed-secret"},
                clock=lambda: self.now,
            )
            with self.subTest(updates=updates), self.assertRaises(WorkloadIdentityError):
                verifier.verify(access_token="opaque-access-token")

    def test_binds_identity_to_expected_task(self):
        with self.assertRaises(WorkloadIdentityError):
            self.verifier.verify(
                access_token="opaque-access-token", expected_task_id="task-other"
            )

    def test_rejects_malformed_scopes_and_claims(self):
        for key, value in (
            ("scope", "task:read  task:progress"),
            ("scope", "task:read task:read"),
            ("agent_id", "../other"),
            ("tenant_id", ""),
            ("jti", " "),
            ("iat", True),
        ):
            claims = dict(self.claims)
            claims[key] = value
            verifier = OAuthWorkloadIdentityVerifier(
                transport=FakeIntrospectionTransport(claims),
                introspection_endpoint="https://identity.example.test/introspect",
                issuer="https://identity.example.test",
                audience="yuki-task-runtime",
                client_id="yuki-workers",
                introspection_headers={"Authorization": "Basic host-managed-secret"},
                clock=lambda: self.now,
            )
            with self.subTest(key=key, value=value), self.assertRaises(WorkloadIdentityError):
                verifier.verify(access_token="opaque-access-token")

    def test_introspection_failure_fails_closed_without_leaking_details(self):
        verifier = OAuthWorkloadIdentityVerifier(
            transport=FakeIntrospectionTransport(RuntimeError("secret backend detail")),
            introspection_endpoint="https://identity.example.test/introspect",
            issuer="https://identity.example.test",
            audience="yuki-task-runtime",
            client_id="yuki-workers",
            introspection_headers={"Authorization": "Basic host-managed-secret"},
            clock=lambda: self.now,
        )
        with self.assertRaisesRegex(WorkloadIdentityError, "workload_introspection_unavailable") as caught:
            verifier.verify(access_token="opaque-access-token")
        self.assertNotIn("secret backend detail", str(caught.exception))

    def test_requires_trusted_transport_and_endpoint_authentication(self):
        with self.assertRaises(TypeError):
            OAuthWorkloadIdentityVerifier(
                transport=object(),
                introspection_endpoint="https://identity.example.test/introspect",
                issuer="https://identity.example.test",
                audience="yuki-task-runtime",
                client_id="yuki-workers",
                introspection_headers={"Authorization": "Basic secret"},
            )
        with self.assertRaises(ValueError):
            OAuthWorkloadIdentityVerifier(
                transport=self.transport,
                introspection_endpoint="https://identity.example.test/introspect",
                issuer="https://identity.example.test",
                audience="yuki-task-runtime",
                client_id="yuki-workers",
                introspection_headers={},
            )

