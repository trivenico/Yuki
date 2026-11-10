import unittest
from urllib.parse import parse_qs, urlsplit

from core.oauth_authorization_service import (
    OAuthAuthorizationService,
    OAuthAuthorizationServiceError,
)


class Transactions:
    def __init__(self):
        self.items = {}
    def create(self, **kwargs):
        self.items[kwargs["state"]] = kwargs
        return 123456
    def consume(self, *, state, owner_id, session_id):
        item = self.items.pop(state)
        if item["owner_id"] != owner_id or item["session_id"] != session_id:
            raise ValueError("scope mismatch")
        from types import SimpleNamespace
        return SimpleNamespace(
            state=state, code_verifier=item["code_verifier"],
            redirect_uri=item["redirect_uri"], issuer=item["issuer"],
            resource=item["resource"], expires_at=123456,
        )


class Vault:
    def __init__(self):
        self.grants = []
    def store(self, **kwargs):
        self.grants.append(kwargs)
        return 1


class Transport:
    def __init__(self):
        self.calls = []
    def request_form(self, endpoint, *, fields):
        self.calls.append((endpoint, fields))
        return {
            "access_token": "access-token-123",
            "refresh_token": "refresh-token-456",
            "token_type": "Bearer",
            "expires_in": 3600,
            "scope": "read",
        }


class IdentityVerifier:
    def __init__(self, subject="provider-subject-1"):
        self.subject = subject
        self.calls = []
    def verify(self, *, access_token):
        self.calls.append(access_token)
        if self.subject != "provider-subject-1":
            raise RuntimeError("account mismatch")
        return {"subject": self.subject}


class OAuthAuthorizationServiceTests(unittest.TestCase):
    def setUp(self):
        self.transactions, self.vault, self.transport = Transactions(), Vault(), Transport()
        self.identity = IdentityVerifier()
        self.service = OAuthAuthorizationService(
            transaction_store=self.transactions, credential_vault=self.vault,
            transport=self.transport, provider_id="upwork", account_id="owner-account",
            client_id="public-client", authorization_endpoint="https://auth.example.com/authorize",
            token_endpoint="https://auth.example.com/token",
            allowed_authorization_hosts={"auth.example.com"},
            redirect_uri="https://yuki.example/oauth/callback",
            issuer="https://auth.example.com", resource="https://api.example.com",
            scopes=["read"], identity_verifier=self.identity,
        )

    def test_begin_and_complete_binds_owner_and_persists_validated_grant(self):
        started = self.service.begin(owner_id="owner-1", session_id="session-1")
        query = parse_qs(urlsplit(started["authorization_url"]).query)
        callback = "https://yuki.example/oauth/callback?code=one-time-code&state=" + query["state"][0]
        result = self.service.complete(
            owner_id="owner-1", session_id="session-1", callback_url=callback,
        )
        self.assertEqual(result["provider_id"], "upwork")
        self.assertEqual(result["credential_version"], 1)
        self.assertEqual(self.vault.grants[0]["owner_id"], "owner-1")
        self.assertEqual(self.vault.grants[0]["access_token"], "access-token-123")
        self.assertEqual(self.transport.calls[0][1]["code"], "one-time-code")
        self.assertNotIn("access-token-123", repr(result))

    def test_callback_origin_and_extra_parameters_rejected_before_consume(self):
        started = self.service.begin(owner_id="owner-1", session_id="session-1")
        state = parse_qs(urlsplit(started["authorization_url"]).query)["state"][0]
        for callback in (
            "https://attacker.example/oauth/callback?code=x&state=" + state,
            "https://yuki.example/oauth/callback?code=x&state=" + state + "&extra=x",
            "https://yuki.example/oauth/callback?code=x&code=y&state=" + state,
        ):
            with self.subTest(callback=callback), self.assertRaises(OAuthAuthorizationServiceError):
                self.service.complete(owner_id="owner-1", session_id="session-1", callback_url=callback)
        self.assertEqual(self.transport.calls, [])

    def test_owner_or_session_mismatch_never_exchanges_code(self):
        started = self.service.begin(owner_id="owner-1", session_id="session-1")
        state = parse_qs(urlsplit(started["authorization_url"]).query)["state"][0]
        callback = "https://yuki.example/oauth/callback?code=x&state=" + state
        with self.assertRaises(OAuthAuthorizationServiceError):
            self.service.complete(owner_id="owner-2", session_id="session-1", callback_url=callback)
        self.assertEqual(self.transport.calls, [])

    def test_exchange_failure_is_sanitized_and_transaction_is_not_reusable(self):
        class BrokenTransport:
            def request_form(self, *args, **kwargs):
                raise RuntimeError("secret authorization code")
        self.service._transport = BrokenTransport()
        started = self.service.begin(owner_id="owner-1", session_id="session-1")
        state = parse_qs(urlsplit(started["authorization_url"]).query)["state"][0]
        callback = "https://yuki.example/oauth/callback?code=x&state=" + state
        with self.assertRaisesRegex(OAuthAuthorizationServiceError, "authorization_exchange_failed") as caught:
            self.service.complete(owner_id="owner-1", session_id="session-1", callback_url=callback)
        self.assertNotIn("secret authorization code", str(caught.exception))
        self.assertEqual(self.transport.calls, [])


    def test_provider_identity_is_verified_before_credential_persistence(self):
        started = self.service.begin(owner_id="owner-1", session_id="session-1")
        state = parse_qs(urlsplit(started["authorization_url"]).query)["state"][0]
        callback = "https://yuki.example/oauth/callback?code=x&state=" + state
        self.service.complete(owner_id="owner-1", session_id="session-1", callback_url=callback)
        self.assertEqual(self.identity.calls, ["access-token-123"])
        self.assertEqual(len(self.vault.grants), 1)

    def test_provider_identity_mismatch_prevents_credential_persistence(self):
        self.service._identity_verifier = IdentityVerifier(subject="attacker-account")
        started = self.service.begin(owner_id="owner-1", session_id="session-1")
        state = parse_qs(urlsplit(started["authorization_url"]).query)["state"][0]
        callback = "https://yuki.example/oauth/callback?code=x&state=" + state
        with self.assertRaisesRegex(OAuthAuthorizationServiceError, "authorization_exchange_failed"):
            self.service.complete(owner_id="owner-1", session_id="session-1", callback_url=callback)
        self.assertEqual(self.vault.grants, [])


if __name__ == "__main__":
    unittest.main()
