import json
import unittest
from types import SimpleNamespace

from core.google_gmail_readonly_client import GmailReadOnlyClient, GmailReadOnlyError
from core.oauth_connection_registry import OAuthConnectionRecord
from core.oauth_provider_credentials import OAuthProviderCredentialResolver


class Connections:
    def get(self, **kwargs):
        return OAuthConnectionRecord(
            "google", "acct-1",
            ("https://www.googleapis.com/auth/gmail.readonly",), 1000,
        )


class Vault:
    def get(self, **kwargs):
        return SimpleNamespace(
            owner_id="owner-1", provider_id="google", account_id="acct-1",
            scopes=("https://www.googleapis.com/auth/gmail.readonly",),
            access_token="secret-token", expires_at=9000, version=1,
        )


class Response:
    def __init__(self, status, document):
        self.status = status
        self.document = document
    def __enter__(self): return self
    def __exit__(self, *_): return False
    def read(self, amount):
        raw = json.dumps(self.document).encode()
        return raw[:amount]


class PinnedTransport:
    allowed_hosts = frozenset({"gmail.googleapis.com"})
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []
    def open(self, request):
        self.requests.append(request)
        return Response(200, self.responses.pop(0))


class GmailReadOnlyClientTests(unittest.TestCase):
    def setUp(self):
        resolver = OAuthProviderCredentialResolver(
            connection_registry=Connections(), credential_vault=Vault(),
            clock=lambda: 1000,
        )
        self.transport = PinnedTransport([])
        self.client = GmailReadOnlyClient(
            credential_resolver=resolver, transport=self.transport,
        )
        self.message_id = "<yuki-action-123@example.com>"

    def test_exact_message_id_is_verified_before_returning_match(self):
        self.transport.responses = [
            {"messages": [{"id": "gmail_123", "threadId": "thread_1"}]},
            {"id": "gmail_123", "threadId": "thread_1",
             "labelIds": ["SENT"],
             "payload": {"headers": [{"name": "Message-ID", "value": self.message_id}]}},
        ]
        result = self.client.find_message_by_rfc822_message_id(
            owner_id="owner-1", account_id="acct-1", message_id=self.message_id,
        )
        self.assertTrue(result["found"])
        self.assertEqual(result["message"]["provider_message_id"], "gmail_123")
        self.assertEqual(len(self.transport.requests), 2)
        self.assertEqual(
            self.transport.requests[0].get_header("Authorization"), "Bearer secret-token",
        )
        self.assertIn("rfc822msgid%3A", self.transport.requests[0].full_url)

    def test_unverified_candidate_is_not_reported_as_match(self):
        self.transport.responses = [
            {"messages": [{"id": "gmail_123"}]},
            {"id": "gmail_123",
             "payload": {"headers": [{"name": "Message-ID", "value": "<other@example.com>"}]}},
        ]
        result = self.client.find_message_by_rfc822_message_id(
            owner_id="owner-1", account_id="acct-1", message_id=self.message_id,
        )
        self.assertFalse(result["found"])
        self.assertIsNone(result["message"])

    def test_invalid_message_id_is_rejected_before_network(self):
        with self.assertRaisesRegex(GmailReadOnlyError, "invalid_message_id"):
            self.client.find_message_by_rfc822_message_id(
                owner_id="owner-1", account_id="acct-1", message_id="injected\r\nheader",
            )
        self.assertEqual(self.transport.requests, [])

    def test_non_success_http_status_fails_closed(self):
        class Rejected(PinnedTransport):
            def open(self, request):
                return Response(403, {"error": "forbidden"})
        self.client._transport = Rejected([])
        with self.assertRaisesRegex(GmailReadOnlyError, "request_rejected"):
            self.client.find_message_by_rfc822_message_id(
                owner_id="owner-1", account_id="acct-1", message_id=self.message_id,
            )

    def test_multiple_verified_matches_are_ambiguous(self):
        self.transport.responses = [
            {"messages": [{"id": "gmail_1"}, {"id": "gmail_2"}]},
            {"id": "gmail_1", "payload": {"headers": [{"name": "Message-ID", "value": self.message_id}]}},
            {"id": "gmail_2", "payload": {"headers": [{"name": "Message-ID", "value": self.message_id}]}},
        ]
        with self.assertRaisesRegex(GmailReadOnlyError, "ambiguous"):
            self.client.find_message_by_rfc822_message_id(
                owner_id="owner-1", account_id="acct-1", message_id=self.message_id,
            )


if __name__ == "__main__":
    unittest.main()
