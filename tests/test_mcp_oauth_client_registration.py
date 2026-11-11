import unittest

from core.mcp_oauth_client_registration import PublicOAuthClientRegistrar
from core.mcp_oauth_metadata import (
    AuthorizationServerMetadata,
    ClientRegistrationPlan,
    OAuthMetadataError,
)


class FakeTransport:
    def __init__(self, response=None, error=None):
        self.response = response or {"client_id": "client-123"}
        self.error = error
        self.calls = []

    def request_json(self, method, endpoint, *, payload=None):
        self.calls.append((method, endpoint, payload))
        if self.error:
            raise self.error
        return self.response


class PublicOAuthClientRegistrarTests(unittest.TestCase):
    def setUp(self):
        self.metadata = AuthorizationServerMetadata(
            issuer="https://auth.example.com",
            authorization_endpoint="https://auth.example.com/authorize",
            token_endpoint="https://auth.example.com/token",
            registration_endpoint="https://auth.example.com/register",
            revocation_endpoint=None,
            code_challenge_methods_supported=("S256",),
            client_id_metadata_document_supported=True,
            authorization_response_iss_parameter_supported=True,
            jwks_uri=None,
            id_token_signing_alg_values_supported=(),
        )
        self.transport = FakeTransport()
        self.registrar = PublicOAuthClientRegistrar(
            transport=self.transport,
            allowed_registration_hosts=("auth.example.com",),
        )
        self.redirect = "https://yuki.example.com/oauth/callback/upwork"
        self.scopes = ("jobs.read", "profile.read")

    def test_preregistered_client_never_calls_network(self):
        result = self.registrar.register(
            plan=ClientRegistrationPlan("pre_registered"),
            metadata=self.metadata,
            redirect_uri=self.redirect,
            scopes=self.scopes,
            preregistered_client_id="registered-yuki-client",
        )
        self.assertEqual(result.client_id, "registered-yuki-client")
        self.assertEqual(result.method, "pre_registered")
        self.assertEqual(self.transport.calls, [])

    def test_cimd_uses_document_uri_as_client_id_without_network(self):
        result = self.registrar.register(
            plan=ClientRegistrationPlan("client_id_metadata_document"),
            metadata=self.metadata,
            redirect_uri=self.redirect,
            scopes=self.scopes,
            cimd_document_uri="https://yuki.example.com/.well-known/oauth-client",
        )
        self.assertEqual(result.client_id, "https://yuki.example.com/.well-known/oauth-client")
        self.assertEqual(result.method, "client_id_metadata_document")
        self.assertEqual(self.transport.calls, [])

    def test_cimd_requires_server_support(self):
        metadata = AuthorizationServerMetadata(
            **{**self.metadata.__dict__, "client_id_metadata_document_supported": False}
        )
        with self.assertRaisesRegex(OAuthMetadataError, "cimd_not_supported"):
            self.registrar.register(
                plan=ClientRegistrationPlan("client_id_metadata_document"),
                metadata=metadata,
                redirect_uri=self.redirect,
                scopes=self.scopes,
                cimd_document_uri="https://yuki.example.com/client.json",
            )

    def test_dcr_posts_public_client_metadata_once(self):
        result = self.registrar.register(
            plan=ClientRegistrationPlan(
                "dynamic_client_registration", self.metadata.registration_endpoint,
            ),
            metadata=self.metadata,
            redirect_uri=self.redirect,
            scopes=self.scopes,
        )
        self.assertEqual(result.client_id, "client-123")
        self.assertEqual(len(self.transport.calls), 1)
        method, endpoint, payload = self.transport.calls[0]
        self.assertEqual((method, endpoint), ("POST", self.metadata.registration_endpoint))
        self.assertEqual(payload["application_type"], "web")
        self.assertEqual(payload["token_endpoint_auth_method"], "none")
        self.assertEqual(payload["redirect_uris"], [self.redirect])
        self.assertEqual(payload["scope"], "jobs.read profile.read")
        self.assertNotIn("client_secret", payload)

    def test_dcr_rejects_unapproved_host_before_network(self):
        registrar = PublicOAuthClientRegistrar(
            transport=self.transport, allowed_registration_hosts=(),
        )
        with self.assertRaisesRegex(OAuthMetadataError, "registration_host_not_allowed"):
            registrar.register(
                plan=ClientRegistrationPlan(
                    "dynamic_client_registration", self.metadata.registration_endpoint,
                ),
                metadata=self.metadata,
                redirect_uri=self.redirect,
                scopes=self.scopes,
            )
        self.assertEqual(self.transport.calls, [])

    def test_dcr_rejects_secret_in_public_client_response(self):
        self.transport.response = {
            "client_id": "client-123",
            "client_secret": "must-not-be-stored",
        }
        with self.assertRaisesRegex(OAuthMetadataError, "unexpected_public_client_secret"):
            self.registrar.register(
                plan=ClientRegistrationPlan(
                    "dynamic_client_registration", self.metadata.registration_endpoint,
                ),
                metadata=self.metadata,
                redirect_uri=self.redirect,
                scopes=self.scopes,
            )

    def test_dcr_rejects_redirect_uri_mutation(self):
        self.transport.response = {
            "client_id": "client-123",
            "redirect_uris": ["https://attacker.example/callback"],
        }
        with self.assertRaisesRegex(OAuthMetadataError, "registered_redirect_uri_mismatch"):
            self.registrar.register(
                plan=ClientRegistrationPlan(
                    "dynamic_client_registration", self.metadata.registration_endpoint,
                ),
                metadata=self.metadata,
                redirect_uri=self.redirect,
                scopes=self.scopes,
            )

    def test_dcr_failure_is_sanitized_and_never_retried(self):
        self.transport.error = RuntimeError("sensitive provider response")
        with self.assertRaisesRegex(OAuthMetadataError, "client_registration_failed"):
            self.registrar.register(
                plan=ClientRegistrationPlan(
                    "dynamic_client_registration", self.metadata.registration_endpoint,
                ),
                metadata=self.metadata,
                redirect_uri=self.redirect,
                scopes=self.scopes,
            )
        self.assertEqual(len(self.transport.calls), 1)


if __name__ == "__main__":
    unittest.main()
