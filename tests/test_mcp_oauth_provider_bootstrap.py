import unittest

from core.mcp_oauth_provider_bootstrap import MCPProviderOAuthBootstrap, OAuthProviderBootstrapConfig
from core.mcp_oauth_metadata import OAuthMetadataError


class Metadata:
    def __init__(self):
        self.calls = []
    def request_json(self, method, url, *, payload=None):
        self.calls.append((method, url, payload))
        if "oauth-protected-resource" in url:
            return {"resource": "https://api.example.com/mcp",
                    "authorization_servers": ["https://auth.example.com"]}
        return {"issuer": "https://auth.example.com",
                "authorization_endpoint": "https://auth.example.com/authorize",
                "token_endpoint": "https://auth.example.com/token",
                "registration_endpoint": "https://auth.example.com/register",
                "code_challenge_methods_supported": ["S256"],
                "client_id_metadata_document_supported": False,
                "authorization_response_iss_parameter_supported": True}


class Registration:
    def __init__(self):
        self.calls = []
    def request_json(self, method, url, *, payload=None):
        self.calls.append((method, url, payload))
        return {"client_id": "public-client", "token_endpoint_auth_method": "none"}


class OAuthTransport:
    def request_form(self, *args, **kwargs):
        raise AssertionError("prepare must not exchange authorization codes")


class Transactions:
    def create(self, **kwargs): return 1
    def consume(self, **kwargs): raise AssertionError("prepare must not consume transactions")


class Vault:
    def store(self, **kwargs): raise AssertionError("prepare must not persist credentials")


class Verifier:
    def verify(self, **kwargs): return {"subject": "account"}


def config(**overrides):
    values = dict(
        provider_id="example", account_id="owner-account",
        resource_uri="https://api.example.com/mcp",
        redirect_uri="https://yuki.example.com/oauth/callback/example",
        scopes=("jobs.read",), application_type="web",
        allowed_metadata_hosts=("api.example.com", "auth.example.com"),
        allowed_registration_hosts=("auth.example.com",),
        allowed_authorization_hosts=("auth.example.com",),
    )
    values.update(overrides)
    return OAuthProviderBootstrapConfig(**values)


class BootstrapTests(unittest.TestCase):
    def setUp(self):
        self.metadata = Metadata()
        self.registration = Registration()
        self.bootstrap = MCPProviderOAuthBootstrap(
            metadata_transport=self.metadata, registration_transport=self.registration,
            oauth_transport=OAuthTransport(), transaction_store=Transactions(),
            credential_vault=Vault(), identity_verifier_for_provider=lambda _: Verifier(),
        )

    def test_composes_discovery_registration_and_authorization_service(self):
        prepared = self.bootstrap.prepare(config())
        self.assertEqual(prepared.client_id, "public-client")
        self.assertEqual(prepared.registration_method, "dynamic_client_registration")
        self.assertEqual(len(self.registration.calls), 1)
        self.assertEqual(self.registration.calls[0][2]["application_type"], "web")
        self.assertEqual(prepared.authorization_service._redirect_uri, config().redirect_uri)

    def test_unapproved_token_host_fails_before_registration(self):
        def response(method, url, **kwargs):
            if "oauth-protected-resource" in url:
                return {"resource": "https://api.example.com/mcp",
                        "authorization_servers": ["https://auth.example.com"]}
            return {"issuer": "https://auth.example.com",
                    "authorization_endpoint": "https://auth.example.com/authorize",
                    "token_endpoint": "https://evil.example/token",
                    "registration_endpoint": "https://auth.example.com/register",
                    "code_challenge_methods_supported": ["S256"]}
        self.metadata.request_json = response
        with self.assertRaisesRegex(OAuthMetadataError, "authorization_endpoint_host_not_allowed"):
            self.bootstrap.prepare(config())
        self.assertEqual(self.registration.calls, [])

    def test_callback_path_is_provider_specific(self):
        with self.assertRaisesRegex(ValueError, "provider-specific"):
            config(redirect_uri="https://yuki.example.com/oauth/callback")

    def test_cimd_does_not_post_registration(self):
        def response(method, url, **kwargs):
            if "oauth-protected-resource" in url:
                return {"resource": "https://api.example.com/mcp",
                        "authorization_servers": ["https://auth.example.com"]}
            return {"issuer": "https://auth.example.com",
                    "authorization_endpoint": "https://auth.example.com/authorize",
                    "token_endpoint": "https://auth.example.com/token",
                    "registration_endpoint": "https://auth.example.com/register",
                    "code_challenge_methods_supported": ["S256"],
                    "client_id_metadata_document_supported": True,
                    "authorization_response_iss_parameter_supported": True}
        self.metadata.request_json = response
        result = self.bootstrap.prepare(config(cimd_document_uri="https://yuki.example.com/client.json"))
        self.assertEqual(result.registration_method, "client_id_metadata_document")
        self.assertEqual(self.registration.calls, [])


if __name__ == "__main__":
    unittest.main()
