import unittest
from urllib.parse import parse_qs, urlsplit

from core.google_oauth_provider import (
    GOOGLE_GMAIL_SCOPES, GOOGLE_HOSTS,
    build_google_gmail_authorization_service, prepare_google_gmail_provider,
    GoogleOAuthConfigurationError,
)


class Store:
    def create(self, **kwargs):
        self.created = kwargs
        return 1234
    def consume(self, **kwargs):
        raise AssertionError("not used")


class Vault:
    def store(self, **kwargs):
        raise AssertionError("not used")


class Transport:
    allowed_hosts = frozenset(GOOGLE_HOSTS)
    def request_form(self, *args, **kwargs):
        raise AssertionError("not used")
    def request_json(self, *args, **kwargs):
        raise AssertionError("not used")


class GoogleOAuthProviderTests(unittest.TestCase):
    def build(self, **overrides):
        values = dict(
            client_id="client-id.apps.googleusercontent.com",
            redirect_uri="https://yuki.example.com/oauth/callback/google",
            transaction_store=Store(), credential_vault=Vault(),
            pinned_transport=Transport(),
            account_id_resolver=lambda claims: "google-" + "a" * 64,
        )
        values.update(overrides)
        return build_google_gmail_authorization_service(**values)

    def test_google_authorization_uses_pkce_offline_and_no_resource_parameter(self):
        service = self.build()
        result = service.begin(owner_id="owner-1", session_id="session-1")
        params = parse_qs(urlsplit(result["authorization_url"]).query)
        self.assertEqual(urlsplit(result["authorization_url"]).netloc, "accounts.google.com")
        self.assertEqual(params["response_type"], ["code"])
        self.assertEqual(params["code_challenge_method"], ["S256"])
        self.assertEqual(params["scope"], [" ".join((*GOOGLE_GMAIL_SCOPES, "openid"))])
        self.assertEqual(params["access_type"], ["offline"])
        self.assertEqual(params["include_granted_scopes"], ["true"])
        self.assertEqual(params["prompt"], ["consent"])
        self.assertNotIn("resource", params)
        self.assertEqual(result["expires_at"], 1234)
        self.assertIsNone(service._oidc_verifier._subject)
        self.assertIsNone(service._identity_verifier._subject)
        self.assertIsNone(service._identity_verifier._audience)

    def test_google_provider_uses_existing_prepared_registry_contract(self):
        prepared = prepare_google_gmail_provider(
            client_id="client-id.apps.googleusercontent.com",
            redirect_uri="https://yuki.example.com/oauth/callback/google",
            transaction_store=Store(), credential_vault=Vault(),
            pinned_transport=Transport(),
            account_id_resolver=lambda claims: "google-" + "a" * 64,
        )
        from core.mcp_oauth_provider_bootstrap import PreparedOAuthProvider
        self.assertIsInstance(prepared, PreparedOAuthProvider)
        self.assertEqual(prepared.provider_id, "google")
        self.assertEqual(prepared.registration_method, "pre-registered")
        self.assertTrue(callable(prepared.authorization_service.begin))
        self.assertTrue(callable(prepared.authorization_service.complete))

    def test_default_account_resolver_hashes_verified_subject_without_email(self):
        prepared = self.build(account_id_resolver=None)
        self.assertTrue(callable(prepared._account_id_resolver))
        account_id = prepared._account_id_resolver({
            "issuer": "https://accounts.google.com", "subject": "stable-subject",
            "audience": "client-id.apps.googleusercontent.com",
        })
        self.assertRegex(account_id, r"^google-[a-f0-9]{64}$")
        self.assertNotIn("stable-subject", account_id)

    def test_requests_only_send_and_readonly_gmail_scopes(self):
        self.assertEqual(set(GOOGLE_GMAIL_SCOPES), {
            "https://www.googleapis.com/auth/gmail.send",
            "https://www.googleapis.com/auth/gmail.readonly",
        })
        self.assertNotIn("https://mail.google.com/", GOOGLE_GMAIL_SCOPES)
        self.assertNotIn("https://www.googleapis.com/auth/gmail.modify", GOOGLE_GMAIL_SCOPES)

    def test_rejects_bad_callback_and_missing_identity_resolver(self):
        for redirect in (
            "http://yuki.example.com/oauth/callback/google",
            "https://attacker.example/oauth/callback/google",
            "https://yuki.example.com/oauth/callback/other",
            "https://yuki.example.com/oauth/callback/google?x=1",
        ):
            with self.subTest(redirect=redirect), self.assertRaises(GoogleOAuthConfigurationError):
                self.build(redirect_uri=redirect)
        with self.assertRaises(GoogleOAuthConfigurationError):
            self.build(account_id_resolver=None)

    def test_requires_every_google_oauth_and_oidc_host_on_pinned_transport(self):
        class Incomplete:
            allowed_hosts = frozenset({"accounts.google.com", "oauth2.googleapis.com"})
        with self.assertRaisesRegex(GoogleOAuthConfigurationError, "transport_host"):
            self.build(pinned_transport=Incomplete())


if __name__ == "__main__":
    unittest.main()
