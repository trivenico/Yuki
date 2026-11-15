import unittest
from urllib.parse import parse_qs, urlsplit

from core.oauth_authorization_service import (
    OAuthAuthorizationService, OAuthAuthorizationServiceError,
)
from core.google_oauth_identity import GoogleAccountIdentityFactory


class Transactions:
    def __init__(self):
        self.items = {}

    def create(self, **kwargs):
        self.items[kwargs["state"]] = kwargs
        return 123456

    def consume(self, *, state, owner_id, session_id):
        from types import SimpleNamespace
        item = self.items.pop(state)
        if item["owner_id"] != owner_id or item["session_id"] != session_id:
            raise ValueError("scope mismatch")
        return SimpleNamespace(
            state=state, code_verifier=item["code_verifier"],
            redirect_uri=item["redirect_uri"], issuer=item["issuer"],
            resource=item["resource"], oidc_nonce=item.get("oidc_nonce"),
        )


class Vault:
    def __init__(self):
        self.grants = []

    def store(self, **kwargs):
        self.grants.append(kwargs)
        return 1


class Transport:
    def request_form(self, endpoint, *, fields):
        return {
            "access_token": "access-token-123",
            "refresh_token": "refresh-token-456",
            "token_type": "Bearer",
            "expires_in": 3600,
            "scope": "https://www.googleapis.com/auth/gmail.send openid",
            "id_token": "header.payload.signature",
        }


class OIDCVerifier:
    def __init__(self, subject="google-subject-123"):
        self.subject = subject

    def verify(self, *, id_token, expected_nonce, access_token=None):
        return {"issuer": "https://accounts.google.com", "subject": self.subject,
                "audience": "yuki-google-client"}


class UserInfoVerifier:
    def __init__(self, subject="google-subject-123"):
        self.subject = subject

    def verify(self, *, access_token):
        return {"issuer": "https://accounts.google.com", "subject": self.subject}


class DynamicGoogleOAuthBindingTests(unittest.TestCase):
    def make_service(self, *, oidc_subject="google-subject-123",
                     userinfo_subject="google-subject-123"):
        self.transactions = Transactions()
        self.vault = Vault()
        factory = GoogleAccountIdentityFactory()
        return OAuthAuthorizationService(
            transaction_store=self.transactions,
            credential_vault=self.vault,
            transport=Transport(),
            provider_id="google",
            account_id=None,
            account_id_resolver=lambda identity: factory.create(identity).account_id,
            client_id="yuki-google-client",
            authorization_endpoint="https://accounts.google.com/o/oauth2/v2/auth",
            token_endpoint="https://oauth2.googleapis.com/token",
            allowed_authorization_hosts={"accounts.google.com", "oauth2.googleapis.com"},
            redirect_uri="https://yuki.example/oauth/callback/google",
            issuer="https://accounts.google.com",
            resource="https://gmail.googleapis.com/",
            scopes=("https://www.googleapis.com/auth/gmail.send",),
            identity_verifier=UserInfoVerifier(userinfo_subject),
            oidc_verifier=OIDCVerifier(oidc_subject),
        )

    def complete(self, service):
        started = service.begin(owner_id="owner-1", session_id="session-1")
        query = parse_qs(urlsplit(started["authorization_url"]).query)
        callback = (
            "https://yuki.example/oauth/callback/google?code=one-time-code&state="
            + query["state"][0]
        )
        return service.complete(
            owner_id="owner-1", session_id="session-1", callback_url=callback,
        )

    def test_persists_grant_under_opaque_verified_subject_derived_account(self):
        service = self.make_service()
        result = self.complete(service)
        expected = GoogleAccountIdentityFactory().create({
            "issuer": "https://accounts.google.com",
            "subject": "google-subject-123",
        }).account_id
        self.assertEqual(result["provider_id"], "google")
        self.assertEqual(result["account_id"], expected)
        self.assertEqual(self.vault.grants[0]["account_id"], expected)
        self.assertEqual(self.vault.grants[0]["owner_id"], "owner-1")

    def test_oidc_and_access_token_userinfo_subject_must_match(self):
        service = self.make_service(
            oidc_subject="google-subject-123",
            userinfo_subject="attacker-subject",
        )
        with self.assertRaisesRegex(
            OAuthAuthorizationServiceError, "authorization_exchange_failed",
        ):
            self.complete(service)
        self.assertEqual(self.vault.grants, [])

    def test_dynamic_account_resolution_requires_oidc_verification(self):
        with self.assertRaisesRegex(ValueError, "dynamic account identity"):
            OAuthAuthorizationService(
                transaction_store=Transactions(), credential_vault=Vault(),
                transport=Transport(), provider_id="google", account_id=None,
                account_id_resolver=lambda identity: "google-account",
                client_id="client",
                authorization_endpoint="https://accounts.google.com/o/oauth2/v2/auth",
                token_endpoint="https://oauth2.googleapis.com/token",
                allowed_authorization_hosts={"accounts.google.com", "oauth2.googleapis.com"},
                redirect_uri="https://yuki.example/oauth/callback/google",
                issuer="https://accounts.google.com",
                resource="https://gmail.googleapis.com/",
                scopes=("https://www.googleapis.com/auth/gmail.send",),
                identity_verifier=UserInfoVerifier(),
            )


if __name__ == "__main__":
    unittest.main()
