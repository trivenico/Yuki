import base64
import hashlib
from urllib.parse import parse_qs, urlsplit
import unittest

from core.oauth2_pkce import (
    OAuthAuthorizationError,
    create_pkce_authorization_request,
    validate_oauth_authorization_callback,
)


class OAuthPKCEPrimitiveTests(unittest.TestCase):
    def _request(self, **overrides):
        values = {
            "authorization_endpoint": "https://auth.example.com/authorize",
            "allowed_authorization_hosts": ("auth.example.com",),
            "client_id": "yuki-client",
            "redirect_uri": "https://yuki.example.com/oauth/callback",
            "scopes": ("profile", "jobs.read"),
            "resource": "https://mcp.example.com/mcp",
        }
        values.update(overrides)
        return create_pkce_authorization_request(**values)

    def test_authorization_request_uses_s256_and_resource_indicator(self):
        request = self._request()
        parsed = urlsplit(request.authorization_url)
        params = parse_qs(parsed.query, strict_parsing=True)
        self.assertEqual(parsed.scheme, "https")
        self.assertEqual(params["response_type"], ["code"])
        self.assertEqual(params["client_id"], ["yuki-client"])
        self.assertEqual(params["redirect_uri"], [request.redirect_uri])
        self.assertEqual(params["scope"], ["profile jobs.read"])
        self.assertEqual(params["state"], [request.state])
        self.assertEqual(params["code_challenge_method"], ["S256"])
        expected = base64.urlsafe_b64encode(
            hashlib.sha256(request.code_verifier.encode("ascii")).digest()
        ).rstrip(b"=").decode("ascii")
        self.assertEqual(request.code_challenge, expected)
        self.assertEqual(params["code_challenge"], [expected])
        self.assertEqual(params["resource"], ["https://mcp.example.com/mcp"])

    def test_oidc_nonce_is_included_only_when_configured(self):
        nonce = "random-oidc-nonce-value-123456"
        request = self._request(oidc_nonce=nonce)
        params = parse_qs(urlsplit(request.authorization_url).query)
        self.assertEqual(params["nonce"], [nonce])
        ordinary = self._request()
        self.assertNotIn("nonce", parse_qs(urlsplit(ordinary.authorization_url).query))

    def test_rejects_invalid_oidc_nonce(self):
        for nonce in ("", "too-short", "contains spaces", "x" * 513):
            with self.subTest(nonce=nonce), self.assertRaises(OAuthAuthorizationError):
                self._request(oidc_nonce=nonce)

    def test_each_request_has_independent_state_and_verifier(self):
        first, second = self._request(), self._request()
        self.assertNotEqual(first.state, second.state)
        self.assertNotEqual(first.code_verifier, second.code_verifier)
        self.assertNotEqual(first.code_challenge, second.code_challenge)

    def test_scope_duplicates_are_removed(self):
        request = self._request(scopes=("jobs.read", "profile", "jobs.read"))
        params = parse_qs(urlsplit(request.authorization_url).query)
        self.assertEqual(params["scope"], ["jobs.read profile"])

    def test_rejects_untrusted_authorization_host_and_non_https(self):
        for endpoint, hosts in (
            ("https://attacker.example/authorize", ("auth.example.com",)),
            ("http://auth.example.com/authorize", ("auth.example.com",)),
            ("https://user@auth.example.com/authorize", ("auth.example.com",)),
            ("https://auth.example.com:444/authorize", ("auth.example.com",)),
            ("https://auth.example.com/authorize?state=attacker", ("auth.example.com",)),
        ):
            with self.subTest(endpoint=endpoint), self.assertRaises(OAuthAuthorizationError):
                self._request(authorization_endpoint=endpoint, allowed_authorization_hosts=hosts)

    def test_rejects_invalid_callback_and_resource_urls(self):
        for field, value in (
            ("redirect_uri", "http://yuki.example.com/callback"),
            ("redirect_uri", "https://user@yuki.example.com/callback"),
            ("redirect_uri", "https://yuki.example.com/callback?state=static"),
            ("resource", "http://mcp.example.com/mcp"),
            ("resource", "https://user@mcp.example.com/mcp"),
        ):
            with self.subTest(field=field, value=value), self.assertRaises(OAuthAuthorizationError):
                self._request(**{field: value})

    def test_rejects_invalid_client_and_scope_values(self):
        for client_id in ("", "has spaces", "x" * 513):
            with self.subTest(client_id=client_id), self.assertRaises(OAuthAuthorizationError):
                self._request(client_id=client_id)
        for scopes in ((), ("",), ("scope with space",), ("x" * 129,), tuple(f"s{i}" for i in range(33))):
            with self.subTest(scopes=scopes), self.assertRaises(OAuthAuthorizationError):
                self._request(scopes=scopes)

    def test_valid_callback_is_returned_after_state_check(self):
        request = self._request()
        callback = validate_oauth_authorization_callback(
            callback_url=request.redirect_uri + "?code=temporary-code&state=" + request.state,
            expected_redirect_uri=request.redirect_uri,
            expected_state=request.state,
        )
        self.assertEqual(callback.code, "temporary-code")
        self.assertEqual(callback.state, request.state)

    def test_rejects_callback_state_mismatch_and_wrong_destination(self):
        request = self._request()
        cases = (
            (request.redirect_uri + "?code=c&state=wrong", request.redirect_uri, request.state),
            ("https://attacker.example/callback?code=c&state=" + request.state,
             request.redirect_uri, request.state),
            (request.redirect_uri + "/other?code=c&state=" + request.state,
             request.redirect_uri, request.state),
        )
        for callback_url, redirect, state in cases:
            with self.subTest(callback_url=callback_url), self.assertRaises(OAuthAuthorizationError):
                validate_oauth_authorization_callback(
                    callback_url=callback_url,
                    expected_redirect_uri=redirect,
                    expected_state=state,
                )

    def test_rejects_duplicate_unexpected_and_error_callback_parameters(self):
        request = self._request()
        callbacks = (
            request.redirect_uri + "?code=a&code=b&state=" + request.state,
            request.redirect_uri + "?code=a&state=" + request.state + "&extra=x",
            request.redirect_uri + "?error=access_denied&state=" + request.state,
            request.redirect_uri + "?code=a&state=" + request.state + "&state=" + request.state,
        )
        for callback_url in callbacks:
            with self.subTest(callback_url=callback_url), self.assertRaises(OAuthAuthorizationError):
                validate_oauth_authorization_callback(
                    callback_url=callback_url,
                    expected_redirect_uri=request.redirect_uri,
                    expected_state=request.state,
                )

    def test_validates_authorization_response_issuer(self):
        request = self._request()
        issuer = "https://auth.example.com"
        callback = validate_oauth_authorization_callback(
            callback_url=request.redirect_uri + "?code=code-1&state=" + request.state + "&iss=" + issuer,
            expected_redirect_uri=request.redirect_uri,
            expected_state=request.state,
            expected_issuer=issuer,
            require_issuer=True,
        )
        self.assertEqual(callback.issuer, issuer)

    def test_rejects_missing_or_mismatched_authorization_response_issuer(self):
        request = self._request()
        issuer = "https://auth.example.com"
        callbacks = (
            request.redirect_uri + "?code=c&state=" + request.state,
            request.redirect_uri + "?code=c&state=" + request.state + "&iss=https%3A%2F%2Fevil.example",
            request.redirect_uri + "?code=c&state=" + request.state + "&iss=" + issuer + "&iss=" + issuer,
        )
        for callback_url in callbacks:
            with self.subTest(callback_url=callback_url), self.assertRaises(OAuthAuthorizationError):
                validate_oauth_authorization_callback(
                    callback_url=callback_url,
                    expected_redirect_uri=request.redirect_uri,
                    expected_state=request.state,
                    expected_issuer=issuer,
                    require_issuer=True,
                )

    def test_optional_issuer_is_still_validated_when_present(self):
        request = self._request()
        with self.assertRaises(OAuthAuthorizationError):
            validate_oauth_authorization_callback(
                callback_url=request.redirect_uri + "?code=c&state=" + request.state + "&iss=https%3A%2F%2Fevil.example",
                expected_redirect_uri=request.redirect_uri,
                expected_state=request.state,
                expected_issuer="https://auth.example.com",
                require_issuer=False,
            )

    def test_rejects_malformed_or_oversized_callback(self):
        request = self._request()
        for callback_url in ("not-a-url", request.redirect_uri + "?code=&state=" + request.state,
                             request.redirect_uri + "?code=" + "x" * 4097 + "&state=" + request.state,
                             request.redirect_uri + "?code=a&state=" + request.state + "#fragment"):
            with self.subTest(callback_url=callback_url), self.assertRaises(OAuthAuthorizationError):
                validate_oauth_authorization_callback(
                    callback_url=callback_url,
                    expected_redirect_uri=request.redirect_uri,
                    expected_state=request.state,
                )


if __name__ == "__main__":
    unittest.main()
