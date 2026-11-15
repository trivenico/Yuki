import unittest
from unittest.mock import Mock

from core.google_gmail_send_reconciliation import GmailSendReconciliationAdapter
from core.google_gmail_readonly_client import GmailReadOnlyClient
from core.pending_tool_action_proposals import PendingToolActionProposal
from core.tool_action_journal import ToolAction


def action(**overrides):
    values = dict(
        action_id="a" * 32, idempotency_key="i" * 48,
        tool_name="gmail.send_email", request_digest="d" * 64,
        state="outcome_unknown", result=None, error_code=None,
        created_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00", version=2,
        lease_owner=None, lease_expires_at=None,
    )
    values.update(overrides)
    return ToolAction(**values)


def proposal(**overrides):
    values = dict(
        proposal_id="proposal", principal_id="owner-1",
        action_id="a" * 32, idempotency_key="i" * 48,
        tool_name="gmail.send_email", request_digest="d" * 64,
        arguments={"account_id": "google-account-1"},
        status="approved", created_at="2026-01-01T00:00:00+00:00",
        expires_at="2027-01-01T00:00:00+00:00", decided_at="2026-01-01T00:00:01+00:00",
        execution_state="unknown",
    )
    values.update(overrides)
    return PendingToolActionProposal(**values)


class GmailSendReconciliationAdapterTests(unittest.TestCase):
    def setUp(self):
        self.client = Mock(spec=GmailReadOnlyClient)
        self.adapter = GmailSendReconciliationAdapter(
            client=self.client, message_id_domain="actions.example.com",
        )

    def test_message_id_is_deterministic_and_host_generated(self):
        self.assertEqual(
            self.adapter.message_id_for_action(action()),
            "<yuki-" + "a" * 32 + "@actions.example.com>",
        )

    def test_verified_message_means_succeeded(self):
        message_id = self.adapter.message_id_for_action(action())
        self.client.find_message_by_rfc822_message_id.return_value = {
            "found": True,
            "message": {
                "provider_message_id": "gmail_123", "thread_id": "thread_1",
                "message_id": message_id, "label_ids": ["SENT"],
            },
        }
        result = self.adapter.reconcile(action=action(), proposal=proposal())
        self.assertEqual(result.outcome, "succeeded")
        self.assertEqual(result.evidence_reference, "gmail:message:gmail_123")
        self.client.find_message_by_rfc822_message_id.assert_called_once_with(
            owner_id="owner-1", account_id="google-account-1", message_id=message_id,
        )

    def test_absent_search_result_remains_unknown_never_not_executed(self):
        self.client.find_message_by_rfc822_message_id.return_value = {
            "found": False, "message": None,
        }
        result = self.adapter.reconcile(action=action(), proposal=proposal())
        self.assertEqual(result.outcome, "unknown")
        self.assertEqual(result.provider_result, {"found": False})

    def test_wrong_action_identity_is_rejected_before_provider_call(self):
        with self.assertRaisesRegex(ValueError, "identity mismatch"):
            self.adapter.reconcile(
                action=action(action_id="b" * 32), proposal=proposal(),
            )
        self.client.find_message_by_rfc822_message_id.assert_not_called()

    def test_account_is_taken_from_exact_proposal_not_default(self):
        self.client.find_message_by_rfc822_message_id.return_value = {
            "found": False, "message": None,
        }
        self.adapter.reconcile(
            action=action(),
            proposal=proposal(arguments={"account_id": "specific-account"}),
        )
        self.assertEqual(
            self.client.find_message_by_rfc822_message_id.call_args.kwargs["account_id"],
            "specific-account",
        )

    def test_invalid_account_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "account identity"):
            self.adapter.reconcile(
                action=action(), proposal=proposal(arguments={"account_id": "../other"}),
            )
        self.client.find_message_by_rfc822_message_id.assert_not_called()

    def test_non_matching_message_id_is_rejected(self):
        self.client.find_message_by_rfc822_message_id.return_value = {
            "found": True,
            "message": {"provider_message_id": "gmail_123", "message_id": "<forged@example.com>"},
        }
        with self.assertRaisesRegex(ValueError, "did not verify"):
            self.adapter.reconcile(action=action(), proposal=proposal())


if __name__ == "__main__":
    unittest.main()
