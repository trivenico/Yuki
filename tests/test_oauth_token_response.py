import unittest

from core.oauth_token_response import (
    OAuthTokenResponseError,
    validate_oauth_token_response,
)


class OAuthTokenResponseTests(unittest.TestCase):
    def setUp(self):
        self.base = {
            "access_token": "opaque-access-token_123",
            "token_type": "Bearer",
            "expires_in": 3600,
            "refresh_token": "opaque-refresh-token_456",
            "scope": "jobs:read profile:read",
        }

    def validate(self, document=None, **kwargs):
        return validate_oauth_token_response(
            self.base if document is None else document,
            requested_scopes=("jobs:read", "profile:read", "jobs:write"),
            expected_issuer="https://login.example",
            expected_resource="https://mcp.example/mcp",
            clock=lambda: 1_800_000_000,
            **kwargs,
        )

    def test_valid_grant_binds_validated_issuer_resource_and_scope(self):
        grant = self.validate()
        self.assertEqual(grant.access_token, "opaque-access-token_123")
        self.assertEqual(grant.refresh_token, "opaque-refresh-token_456")
        self.assertEqual(grant.token_type, "Bearer")
        self.assertEqual(grant.expires_at, 1_800_003_600)
        self.assertEqual(grant.granted_scopes, ("jobs:read", "profile:read"))
        self.assertEqual(grant.issuer, "https://login.example")
        self.assertEqual(grant.resource, "https://mcp.example/mcp")

    def test_token_values_are_not_exposed_by_repr(self):
        grant = self.validate()
        self.assertNotIn(grant.access_token, repr(grant))
        self.assertNotIn(grant.refresh_token, repr(grant))

    def test_omitted_scope_means_requested_scope_and_refresh_is_optional(self):
        document = {"access_token": "token123", "token_type": "bearer", "expires_in": 30}
        grant = self.validate(document)
        self.assertEqual(grant.granted_scopes, ("jobs:read", "profile:read", "jobs:write"))
        self.assertIsNone(grant.refresh_token)

    def test_rejects_widened_or_malformed_granted_scope(self):
        for scope in ("jobs:read admin:all", "jobs:read  profile:read", "jobs:read jobs:read", ""):
            with self.subTest(scope=scope), self.assertRaises(OAuthTokenResponseError):
                self.validate({**self.base, "scope": scope})

    def test_accepts_compact_id_token_without_exposing_it_in_repr(self):
        token = "header.payload.signature"
        grant = self.validate({**self.base, "id_token": token})
        self.assertEqual(grant.id_token, token)
        self.assertNotIn(token, repr(grant))

    def test_rejects_malformed_or_oversized_id_token(self):
        for token in ("not-a-jwt", "a.b", "a.b.c.extra", "x" * 32769, "a b.c.d"):
            with self.subTest(token=token), self.assertRaisesRegex(
                OAuthTokenResponseError, "invalid_id_token"
            ):
                self.validate({**self.base, "id_token": token})

    def test_rejects_missing_or_malformed_access_token(self):
        for token in (None, "", "has space", "line\\nbreak", "bad,token", "x" * 8193):
            document = dict(self.base)
            if token is None:
                document.pop("access_token")
            else:
                document["access_token"] = token
            with self.subTest(token=token), self.assertRaises(OAuthTokenResponseError):
                self.validate(document)

    def test_rejects_invalid_refresh_token(self):
        for token in ("bad token", "x" * 8193, 123):
            with self.subTest(token=token), self.assertRaises(OAuthTokenResponseError):
                self.validate({**self.base, "refresh_token": token})

    def test_rejects_unsupported_token_type_and_lifetime(self):
        for document in (
            {**self.base, "token_type": "DPoP"},
            {**self.base, "expires_in": True},
            {**self.base, "expires_in": 0},
            {**self.base, "expires_in": 31_536_001},
        ):
            with self.subTest(document=document), self.assertRaises(OAuthTokenResponseError):
                self.validate(document)

    def test_rejects_unknown_fields_and_invalid_context(self):
        with self.assertRaisesRegex(OAuthTokenResponseError, "invalid_id_token"):
            self.validate({**self.base, "id_token": "jwt"})
        with self.assertRaises(OAuthTokenResponseError):
            validate_oauth_token_response(
                self.base, requested_scopes=("jobs:read",), expected_issuer="",
                expected_resource="https://mcp.example",
            )

    def test_rejects_invalid_requested_scopes_and_overflowing_expiry(self):
        with self.assertRaises(OAuthTokenResponseError):
            validate_oauth_token_response(
                self.base, requested_scopes=("jobs:read", "jobs:read"),
                expected_issuer="https://login.example",
                expected_resource="https://mcp.example",
            )
        with self.assertRaises(OAuthTokenResponseError):
            validate_oauth_token_response(
                {**self.base, "expires_in": 100},
                requested_scopes=("jobs:read", "profile:read"),
                expected_issuer="https://login.example",
                expected_resource="https://mcp.example",
                clock=lambda: 9_223_372_036_854_775_800,
            )


if __name__ == "__main__":
    unittest.main()
