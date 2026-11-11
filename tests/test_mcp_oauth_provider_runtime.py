import unittest
from types import SimpleNamespace
from unittest.mock import patch

from core.mcp_oauth_metadata import (
    AuthorizationServerMetadata,
    ClientRegistrationPlan,
)
from core.mcp_oauth_provider_runtime import (
    MCPOAuthProviderConfig,
    MCPOAuthProviderConfigurationError,
    build_mcp_oauth_authorization_service,
)


class OAuthProviderRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.config = MCPOAuthProviderConfig(
            provider_id="provider-1",
            account_id="account-1",
            resource="https://mcp.example.test/mcp",
            redirect_uri="https://yuki.example.test/oauth/callback/provider-1",
            scopes=("read", "profile"),
            application_type="web",
            metadata_hosts=("mcp.example.test", "issuer.example.test"),
            registration_hosts=("issuer.example.test",),
            authorization_hosts=("login.example.test",),
            redirect_hosts=("yuki.example.test",),
            expected_issuer="https://issuer.example.test",
            preregistered_client_id="yuki-client",
        )
        self.metadata = AuthorizationServerMetadata(
            issuer="https://issuer.example.test",
            authorization_endpoint="https://login.example.test/authorize",
            token_endpoint="https://issuer.example.test/token",
            registration_endpoint=None,
            revocation_endpoint=None,
            code_challenge_methods_supported=("S256",),
            client_id_metadata_document_supported=False,
            authorization_response_iss_parameter_supported=True,
            jwks_uri=None,
            id_token_signing_alg_values_supported=(),
        )
        self.discovery_result = SimpleNamespace(
            registration=ClientRegistrationPlan("pre_registered"),
            authorization_server_metadata=self.metadata,
        )
        self.transport = SimpleNamespace(request_form=lambda *args, **kwargs: {})
        self.transactions = SimpleNamespace(create=lambda **kwargs: 1, consume=lambda **kwargs: None)
        self.vault = SimpleNamespace(store=lambda **kwargs: 1)
        self.identity = SimpleNamespace(verify=lambda **kwargs: {})

    @patch("core.mcp_oauth_provider_runtime.PublicOAuthClientRegistrar")
    @patch("core.mcp_oauth_provider_runtime.TrustedMCPOAuthDiscovery")
    def test_composes_discovery_registration_and_authorization(
        self, discovery_cls, registrar_cls
    ):
        discovery_cls.return_value.discover.return_value = self.discovery_result
        registrar_cls.return_value.register.return_value = SimpleNamespace(
            client_id="yuki-client"
        )

        service = build_mcp_oauth_authorization_service(
            self.config,
            transport=self.transport,
            transaction_store=self.transactions,
            credential_vault=self.vault,
            identity_verifier=self.identity,
        )

        self.assertEqual(service._provider_id, "provider-1")
        self.assertEqual(service._account_id, "account-1")
        self.assertEqual(service._client_id, "yuki-client")
        discovery_cls.assert_called_once()
        discovery_cls.return_value.discover.assert_called_once_with(
            resource="https://mcp.example.test/mcp",
            expected_issuer="https://issuer.example.test",
            preregistered_client_id="yuki-client",
        )
        registrar_cls.return_value.register.assert_called_once()
        self.assertEqual(
            service._allowed_authorization_hosts, ("login.example.test",)
        )

    @patch("core.mcp_oauth_provider_runtime.TrustedMCPOAuthDiscovery")
    def test_rejects_redirect_host_before_discovery(self, discovery_cls):
        unsafe = MCPOAuthProviderConfig(
            **{**self.config.__dict__, "redirect_hosts": ("attacker.example",)}
        )
        with self.assertRaisesRegex(
            MCPOAuthProviderConfigurationError, "redirect_host_not_allowed"
        ):
            build_mcp_oauth_authorization_service(
                unsafe,
                transport=self.transport,
                transaction_store=self.transactions,
                credential_vault=self.vault,
                identity_verifier=self.identity,
            )
        discovery_cls.assert_not_called()

    @patch("core.mcp_oauth_provider_runtime.TrustedMCPOAuthDiscovery")
    def test_rejects_implicit_or_invalid_deployment_type(self, discovery_cls):
        unsafe = MCPOAuthProviderConfig(
            **{**self.config.__dict__, "application_type": "desktop"}
        )
        with self.assertRaisesRegex(
            MCPOAuthProviderConfigurationError, "invalid_application_type"
        ):
            build_mcp_oauth_authorization_service(
                unsafe,
                transport=self.transport,
                transaction_store=self.transactions,
                credential_vault=self.vault,
                identity_verifier=self.identity,
            )
        discovery_cls.assert_not_called()

    @patch("core.mcp_oauth_provider_runtime.TrustedMCPOAuthDiscovery")
    def test_rejects_insecure_redirect(self, discovery_cls):
        unsafe = MCPOAuthProviderConfig(
            **{**self.config.__dict__, "redirect_uri": "http://yuki.example.test/callback"}
        )
        with self.assertRaisesRegex(
            MCPOAuthProviderConfigurationError, "invalid_redirect_uri"
        ):
            build_mcp_oauth_authorization_service(
                unsafe,
                transport=self.transport,
                transaction_store=self.transactions,
                credential_vault=self.vault,
                identity_verifier=self.identity,
            )
        discovery_cls.assert_not_called()


if __name__ == "__main__":
    unittest.main()
