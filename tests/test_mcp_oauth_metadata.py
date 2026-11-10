import unittest

from core.mcp_oauth_metadata import (
    OAuthMetadataError,
    authorization_server_metadata_urls,
    choose_client_registration,
    protected_resource_metadata_urls,
    validate_authorization_server_metadata,
    validate_protected_resource_metadata,
)


class MCPOAuthMetadataTests(unittest.TestCase):
    def _as_metadata(self, **overrides):
        value = {
            "issuer": "https://login.example.com/tenant",
            "authorization_endpoint": "https://login.example.com/tenant/authorize",
            "token_endpoint": "https://login.example.com/tenant/token",
            "registration_endpoint": "https://login.example.com/tenant/register",
            "code_challenge_methods_supported": ["S256"],
            "client_id_metadata_document_supported": False,
            "authorization_response_iss_parameter_supported": True,
        }
        value.update(overrides)
        return value

    def test_protected_resource_discovery_prefers_path_specific_then_root(self):
        self.assertEqual(
            protected_resource_metadata_urls("https://mcp.example.com/public/mcp"),
            (
                "https://mcp.example.com/.well-known/oauth-protected-resource/public/mcp",
                "https://mcp.example.com/.well-known/oauth-protected-resource",
            ),
        )

    def test_root_resource_has_one_metadata_url(self):
        self.assertEqual(
            protected_resource_metadata_urls("https://mcp.example.com/"),
            ("https://mcp.example.com/.well-known/oauth-protected-resource",),
        )

    def test_rejects_unsafe_resource_url(self):
        for value in (
            "http://mcp.example.com/mcp",
            "https://user@mcp.example.com/mcp",
            "https://mcp.example.com:444/mcp",
            "https://mcp.example.com/mcp?tenant=x",
            "https://mcp.example.com/mcp#fragment",
        ):
            with self.subTest(value=value), self.assertRaises(OAuthMetadataError):
                protected_resource_metadata_urls(value)

    def test_authorization_server_discovery_supports_rfc8414_and_oidc_paths(self):
        self.assertEqual(
            authorization_server_metadata_urls("https://login.example.com/tenant"),
            (
                "https://login.example.com/.well-known/oauth-authorization-server/tenant",
                "https://login.example.com/tenant/.well-known/openid-configuration",
            ),
        )

    def test_protected_resource_document_requires_exact_resource_and_server(self):
        result = validate_protected_resource_metadata(
            {
                "resource": "https://mcp.example.com/public/mcp",
                "authorization_servers": ["https://login.example.com/tenant"],
                "scopes_supported": ["jobs.read", "profile"],
            },
            expected_resource="https://mcp.example.com/public/mcp",
        )
        self.assertEqual(result.authorization_servers, ("https://login.example.com/tenant",))
        self.assertEqual(result.scopes_supported, ("jobs.read", "profile"))

    def test_rejects_resource_mismatch_and_invalid_authorization_servers(self):
        cases = (
            {"resource": "https://other.example/mcp", "authorization_servers": ["https://login.example.com"]},
            {"resource": "https://mcp.example.com/mcp", "authorization_servers": []},
            {"resource": "https://mcp.example.com/mcp", "authorization_servers": ["http://login.example.com"]},
            {"resource": "https://mcp.example.com/mcp", "authorization_servers": ["https://user@login.example.com"]},
            {"resource": "https://mcp.example.com/mcp", "authorization_servers": ["https://login.example.com"] * 11},
        )
        for document in cases:
            with self.subTest(document=document), self.assertRaises(OAuthMetadataError):
                validate_protected_resource_metadata(
                    document, expected_resource="https://mcp.example.com/mcp"
                )

    def test_authorization_server_metadata_is_bound_to_expected_issuer(self):
        metadata = validate_authorization_server_metadata(
            self._as_metadata(),
            expected_issuer="https://login.example.com/tenant",
        )
        self.assertEqual(metadata.issuer, "https://login.example.com/tenant")
        self.assertEqual(metadata.authorization_endpoint, "https://login.example.com/tenant/authorize")
        self.assertEqual(metadata.token_endpoint, "https://login.example.com/tenant/token")
        self.assertTrue(metadata.authorization_response_iss_parameter_supported)

    def test_rejects_issuer_mismatch_and_non_https_endpoints(self):
        for overrides in (
            {"issuer": "https://attacker.example/tenant"},
            {"authorization_endpoint": "http://login.example.com/authorize"},
            {"token_endpoint": "https://user@login.example.com/token"},
            {"registration_endpoint": "https://login.example.com:444/register"},
        ):
            with self.subTest(overrides=overrides), self.assertRaises(OAuthMetadataError):
                validate_authorization_server_metadata(
                    self._as_metadata(**overrides),
                    expected_issuer="https://login.example.com/tenant",
                )

    def test_requires_s256_without_downgrade(self):
        for methods in ([], ["plain"], ["S256", "plain"]):
            if methods == ["S256", "plain"]:
                continue
            with self.subTest(methods=methods), self.assertRaises(OAuthMetadataError):
                validate_authorization_server_metadata(
                    self._as_metadata(code_challenge_methods_supported=methods),
                    expected_issuer="https://login.example.com/tenant",
                )

    def test_rejects_wrongly_typed_capability_flags(self):
        for value in ("true", 1, None):
            with self.subTest(value=value), self.assertRaises(OAuthMetadataError):
                validate_authorization_server_metadata(
                    self._as_metadata(client_id_metadata_document_supported=value),
                    expected_issuer="https://login.example.com/tenant",
                )

    def test_oidc_metadata_requires_trusted_jwks_uri_and_supported_algorithms(self):
        metadata = validate_authorization_server_metadata(
            self._as_metadata(
                jwks_uri="https://login.example.com/tenant/keys",
                id_token_signing_alg_values_supported=["RS256", "ES256", "HS256"],
            ),
            expected_issuer="https://login.example.com/tenant",
        )
        self.assertEqual(metadata.jwks_uri, "https://login.example.com/tenant/keys")
        self.assertEqual(metadata.id_token_signing_alg_values_supported, ("RS256", "ES256"))

    def test_rejects_oidc_metadata_with_missing_jwks_or_no_supported_algorithm(self):
        for overrides in (
            {"jwks_uri": "https://login.example.com/keys"},
            {"id_token_signing_alg_values_supported": ["RS256"]},
            {"jwks_uri": "http://login.example.com/keys",
             "id_token_signing_alg_values_supported": ["RS256"]},
            {"jwks_uri": "https://login.example.com/keys",
             "id_token_signing_alg_values_supported": ["HS256", "none"]},
        ):
            with self.subTest(overrides=overrides), self.assertRaises(OAuthMetadataError):
                validate_authorization_server_metadata(
                    self._as_metadata(**overrides),
                    expected_issuer="https://login.example.com/tenant",
                )

    def test_registration_priority_prefers_preregistered_then_cimd_then_dcr(self):
        dcr = validate_authorization_server_metadata(
            self._as_metadata(), expected_issuer="https://login.example.com/tenant"
        )
        self.assertEqual(
            choose_client_registration(dcr, preregistered_client_id="registered-yuki").method,
            "pre_registered",
        )
        self.assertEqual(
            choose_client_registration(dcr).method,
            "dynamic_client_registration",
        )
        cimd = validate_authorization_server_metadata(
            self._as_metadata(client_id_metadata_document_supported=True),
            expected_issuer="https://login.example.com/tenant",
        )
        self.assertEqual(
            choose_client_registration(cimd).method,
            "client_id_metadata_document",
        )

    def test_registration_fails_closed_when_no_method_is_available(self):
        metadata = validate_authorization_server_metadata(
            self._as_metadata(registration_endpoint=None),
            expected_issuer="https://login.example.com/tenant",
        )
        with self.assertRaisesRegex(OAuthMetadataError, "client_registration_unavailable"):
            choose_client_registration(metadata)

    def test_preregistered_client_id_is_validated(self):
        metadata = validate_authorization_server_metadata(
            self._as_metadata(), expected_issuer="https://login.example.com/tenant"
        )
        for value in ("", "contains spaces", "x" * 513):
            with self.subTest(value=value), self.assertRaises(OAuthMetadataError):
                choose_client_registration(metadata, preregistered_client_id=value)


if __name__ == "__main__":
    unittest.main()
