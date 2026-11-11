import unittest

from core.mcp_oauth_discovery import TrustedMCPOAuthDiscovery
from core.mcp_oauth_metadata import OAuthMetadataError


class FakeTransport:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def request_json(self, method, url):
        self.calls.append((method, url))
        value = self.responses.get(url)
        if isinstance(value, Exception):
            raise value
        if value is None:
            raise OSError("network unavailable")
        return value


class TrustedMCPOAuthDiscoveryTests(unittest.TestCase):
    resource = "https://mcp.example.com/mcp"
    issuer = "https://login.example.com/tenant"

    def _documents(self):
        return {
            "https://mcp.example.com/.well-known/oauth-protected-resource/mcp": {
                "resource": self.resource,
                "authorization_servers": [self.issuer],
                "scopes_supported": ["jobs.read"],
            },
            "https://login.example.com/.well-known/oauth-authorization-server/tenant": {
                "issuer": self.issuer,
                "authorization_endpoint": self.issuer + "/authorize",
                "token_endpoint": self.issuer + "/token",
                "registration_endpoint": self.issuer + "/register",
                "code_challenge_methods_supported": ["S256"],
                "client_id_metadata_document_supported": False,
                "authorization_response_iss_parameter_supported": True,
            },
        }

    def _discovery(self, transport, hosts=("mcp.example.com", "login.example.com")):
        return TrustedMCPOAuthDiscovery(
            transport=transport, allowed_metadata_hosts=hosts,
        )

    def test_discovers_exact_resource_and_issuer_and_selects_dcr(self):
        transport = FakeTransport(self._documents())
        result = self._discovery(transport).discover(resource=self.resource)
        self.assertEqual(result.resource_metadata.resource, self.resource)
        self.assertEqual(result.authorization_server_metadata.issuer, self.issuer)
        self.assertEqual(result.registration.method, "dynamic_client_registration")
        self.assertEqual(
            [method for method, _ in transport.calls],
            ["GET", "GET"],
        )

    def test_uses_root_metadata_url_only_after_path_specific_network_failure(self):
        documents = self._documents()
        documents.pop("https://mcp.example.com/.well-known/oauth-protected-resource/mcp")
        documents["https://mcp.example.com/.well-known/oauth-protected-resource"] = {
            "resource": self.resource,
            "authorization_servers": [self.issuer],
        }
        transport = FakeTransport(documents)
        result = self._discovery(transport).discover(resource=self.resource)
        self.assertEqual(
            result.resource_metadata_url,
            "https://mcp.example.com/.well-known/oauth-protected-resource",
        )
        self.assertEqual(len(transport.calls), 3)

    def test_rejects_unapproved_authorization_server_before_second_network_access(self):
        transport = FakeTransport(self._documents())
        discovery = self._discovery(transport, hosts=("mcp.example.com",))
        with self.assertRaisesRegex(OAuthMetadataError, "metadata_host_not_allowed"):
            discovery.discover(resource=self.resource)
        self.assertEqual(len(transport.calls), 1)
        self.assertEqual(transport.calls[0][0], "GET")

    def test_requires_explicit_selection_when_resource_advertises_multiple_issuers(self):
        documents = self._documents()
        documents["https://mcp.example.com/.well-known/oauth-protected-resource/mcp"][
            "authorization_servers"
        ] = [self.issuer, "https://other-login.example.com"]
        transport = FakeTransport(documents)
        discovery = self._discovery(
            transport, hosts=("mcp.example.com", "login.example.com", "other-login.example.com")
        )
        with self.assertRaisesRegex(OAuthMetadataError, "authorization_server_selection_required"):
            discovery.discover(resource=self.resource)

    def test_expected_issuer_must_be_advertised(self):
        transport = FakeTransport(self._documents())
        with self.assertRaisesRegex(OAuthMetadataError, "authorization_server_not_advertised"):
            self._discovery(transport).discover(
                resource=self.resource, expected_issuer="https://attacker.example"
            )
        self.assertEqual(len(transport.calls), 1)

    def test_malformed_path_specific_document_does_not_fall_back(self):
        documents = self._documents()
        documents["https://mcp.example.com/.well-known/oauth-protected-resource/mcp"] = {
            "resource": "https://attacker.example/mcp",
            "authorization_servers": [self.issuer],
        }
        transport = FakeTransport(documents)
        with self.assertRaisesRegex(OAuthMetadataError, "protected_resource_mismatch"):
            self._discovery(transport).discover(resource=self.resource)
        self.assertEqual(len(transport.calls), 1)

    def test_registration_uses_cimd_before_dcr(self):
        documents = self._documents()
        server = documents["https://login.example.com/.well-known/oauth-authorization-server/tenant"]
        server["client_id_metadata_document_supported"] = True
        transport = FakeTransport(documents)
        result = self._discovery(transport).discover(resource=self.resource)
        self.assertEqual(result.registration.method, "client_id_metadata_document")
        self.assertIsNone(result.registration.endpoint)

    def test_preregistered_client_id_has_highest_priority(self):
        transport = FakeTransport(self._documents())
        result = self._discovery(transport).discover(
            resource=self.resource, preregistered_client_id="yuki-reviewed-client",
        )
        self.assertEqual(result.registration.method, "pre_registered")


if __name__ == "__main__":
    unittest.main()
