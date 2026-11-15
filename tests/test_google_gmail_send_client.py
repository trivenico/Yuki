import base64
import json
import unittest
from email import message_from_bytes, policy
from types import SimpleNamespace

from core.google_gmail_send_client import GmailSendClient, GmailSendError
from core.oauth_connection_registry import OAuthConnectionRecord
from core.oauth_provider_credentials import OAuthProviderCredentialResolver
from core.tool_action_journal import ToolAction


class Connections:
    def get(self, **kwargs):
        return OAuthConnectionRecord(
            "google", "acct-1",
            ("https://www.googleapis.com/auth/gmail.send",), 1000,
        )


class Vault:
    def get(self, **kwargs):
        return SimpleNamespace(
            owner_id="owner-1", provider_id="google", account_id="acct-1",
            scopes=("https://www.googleapis.com/auth/gmail.send",),
            access_token="secret-token", expires_at=9000, version=1,
        )


class Response:
    def __init__(self, status, document):
        self.status = status
        self.document = document

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self, amount):
        raw = json.dumps(self.document).encode()
        return raw[:amount]


class PinnedTransport:
    allowed_hosts = frozenset({"gmail.googleapis.com"})

    def __init__(self, response=None):
        self.response = response or Response(200, {"id": "gmail_123", "threadId": "thread_1"})
        self.requests = []

    def open(self, request):
        self.requests.append(request)
        return self.response


def action(**overrides):
    values = dict(
        action_id="a" * 32, idempotency_key="i" * 48,
        tool_name="gmail.send_email", request_digest="d" * 64,
        state="executing", result=None, error_code=None,
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00", version=1,
        lease_owner="lease", lease_expires_at="2026-01-01T00:02:00+00:00",
    )
    values.update(overrides)
    return ToolAction(**values)


class GmailSendClientTests(unittest.TestCase):
    def setUp(self):
        resolver = OAuthProviderCredentialResolver(
            connection_registry=Connections(), credential_vault=Vault(),
            clock=lambda: 1000,
        )
        self.transport = PinnedTransport()
        self.client = GmailSendClient(
            credential_resolver=resolver, transport=self.transport,
            message_id_domain="actions.example.com",
        )

    def test_sends_one_action_bound_message_through_pinned_transport(self):
        result = self.client.send_email(
            owner_id="owner-1", account_id="acct-1", action=action(),
            to=["person@example.com"], subject="Hello", body="A short message",
        )
        self.assertEqual(result["provider_message_id"], "gmail_123")
        self.assertEqual(result["message_id"], "<yuki-" + "a" * 32 + "@actions.example.com>")
        self.assertEqual(len(self.transport.requests), 1)
        request = self.transport.requests[0]
        self.assertEqual(request.get_method(), "POST")
        self.assertTrue(request.full_url.endswith("/gmail/v1/users/me/messages/send"))
        self.assertEqual(request.get_header("Authorization"), "Bearer secret-token")
        payload = json.loads(request.data.decode())
        raw = base64.urlsafe_b64decode(payload["raw"] + "=" * (-len(payload["raw"]) % 4))
        message = message_from_bytes(raw, policy=policy.default)
        self.assertEqual(message["Message-ID"], result["message_id"])
        self.assertEqual(message["To"], "person@example.com")
        self.assertEqual(message["Subject"], "Hello")
        self.assertEqual(message.get_content(), "A short message\r\n")

    def test_message_id_matches_reconciliation_adapter_contract(self):
        self.assertEqual(
            self.client.message_id_for_action(action()),
            "<yuki-" + "a" * 32 + "@actions.example.com>",
        )

    def test_invalid_action_identity_is_rejected_before_network(self):
        with self.assertRaisesRegex(GmailSendError, "action_identity"):
            self.client.send_email(
                owner_id="owner-1", account_id="acct-1",
                action=action(tool_name="gmail.read_email"),
                to="person@example.com", subject="Hello", body="Body",
            )
        self.assertEqual(self.transport.requests, [])

    def test_header_injection_and_invalid_recipient_are_rejected(self):
        for kwargs in (
            {"to": "person@example.com\\r\\nBcc:evil@example.com", "subject": "Hello", "body": "Body"},
            {"to": "not-an-email", "subject": "Hello", "body": "Body"},
            {"to": "person@example.com", "subject": "Hello\\r\\nBcc:evil@example.com", "body": "Body"},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(GmailSendError):
                self.client.send_email(
                    owner_id="owner-1", account_id="acct-1", action=action(), **kwargs,
                )
        self.assertEqual(self.transport.requests, [])

    def test_provider_error_is_unknown_and_never_retried(self):
        self.transport.response = Response(503, {"error": "unavailable"})
        with self.assertRaisesRegex(GmailSendError, "outcome_unknown"):
            self.client.send_email(
                owner_id="owner-1", account_id="acct-1", action=action(),
                to="person@example.com", subject="Hello", body="Body",
            )
        self.assertEqual(len(self.transport.requests), 1)

    def test_transport_timeout_is_unknown_and_never_retried(self):
        class TimeoutTransport(PinnedTransport):
            def open(self, request):
                self.requests.append(request)
                raise TimeoutError("simulated timeout")
        transport = TimeoutTransport()
        self.client._transport = transport
        with self.assertRaisesRegex(GmailSendError, "outcome_unknown"):
            self.client.send_email(
                owner_id="owner-1", account_id="acct-1", action=action(),
                to="person@example.com", subject="Hello", body="Body",
            )
        self.assertEqual(len(transport.requests), 1)

    def test_unexpected_account_is_rejected_before_network(self):
        with self.assertRaises(GmailSendError):
            self.client.send_email(
                owner_id="owner-1", account_id="different-account", action=action(),
                to="person@example.com", subject="Hello", body="Body",
            )
        self.assertEqual(self.transport.requests, [])


if __name__ == "__main__":
    unittest.main()
