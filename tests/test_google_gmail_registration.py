import unittest
import tempfile
from pathlib import Path

from core.google_gmail_runtime import build_gmail_tool_runtime_bundle
from core.pending_tool_action_proposals import SQLitePendingToolActionProposalStore
from core.tool_action_approvals import SQLiteToolActionApprovalStore
from core.tool_action_journal import ToolActionJournal
from core.tool_action_context import ToolActionContext
from core.approved_tool_action_executor import ApprovedToolActionExecutor
from core.tool_capabilities import ToolCapabilityCatalog

from core.google_gmail_send_tool import (
    GMAIL_SEND_TOOL_NAME, build_gmail_send_tool_registration,
    gmail_send_tool_capability,
)
from core.google_gmail_send_reconciliation import (
    GmailSendReconciliationAdapter, build_gmail_send_reconciliation_registration,
)
from core.tool_action_reconciliation import ToolActionReconciliationRegistry
from core.google_gmail_send_client import GmailSendClient
from core.google_gmail_readonly_client import GmailReadOnlyClient
from core.tool_capabilities import ToolEffect


class StubSendClient(GmailSendClient):
    def __init__(self):
        self.calls = []
    def send_email(self, **kwargs):
        self.calls.append(kwargs)
        return {"provider_message_id": "message123", "thread_id": None,
                "message_id": "<yuki-" + kwargs["action"].action_id + "@mail.example.com>"}


class StubReadClient(GmailReadOnlyClient):
    def __init__(self):
        pass
    def find_message_by_rfc822_message_id(self, **kwargs):
        return {"found": False}


class GmailRegistrationTests(unittest.TestCase):
    def test_send_registration_binds_exact_tool_schema_and_capability(self):
        registration = build_gmail_send_tool_registration(StubSendClient())
        self.assertEqual(registration["name"], GMAIL_SEND_TOOL_NAME)
        self.assertEqual(registration["definition"]["function"]["name"], GMAIL_SEND_TOOL_NAME)
        self.assertIs(registration["capability"].effect, ToolEffect.SIDE_EFFECTING)
        self.assertTrue(registration["capability"].requires_user_confirmation)
        self.assertTrue(registration["capability"].supports_reconciliation)
        self.assertFalse(registration["capability"].provider_idempotency)

    def test_composition_bundle_executes_only_after_durable_approval(self):
        class Policy:
            def authorize_tool(self, name):
                return name == GMAIL_SEND_TOOL_NAME

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            proposals = SQLitePendingToolActionProposalStore(str(root / "proposals.sqlite"))
            approvals = SQLiteToolActionApprovalStore(str(root / "approvals.sqlite"))
            journal = ToolActionJournal(str(root / "journal.sqlite"))
            send_client = StubSendClient()
            bundle = build_gmail_tool_runtime_bundle(
                existing_tools={}, existing_capabilities=ToolCapabilityCatalog({}),
                existing_tool_definitions=(), policy=Policy(), action_journal=journal,
                approval_store=approvals, proposals=proposals,
                send_client=send_client, readonly_client=StubReadClient(),
                message_id_domain="mail.example.com",
            )
            executor = ApprovedToolActionExecutor(proposals, approvals, bundle.guarded_runtime)
            arguments = {
                "account_id": "google-account-1", "to": "person@example.com",
                "subject": "Hello", "body": "Yuki integration test",
            }
            context = ToolActionContext.create("owner-1", GMAIL_SEND_TOOL_NAME, arguments)
            proposal = proposals.create(context, arguments, principal_id="owner-1")
            with self.assertRaisesRegex(ValueError, "not been approved"):
                executor.execute(proposal.proposal_id, principal_id="owner-1")
            proposals.decide(proposal.proposal_id, principal_id="owner-1", decision="approved")
            result = executor.execute(proposal.proposal_id, principal_id="owner-1")
            self.assertEqual(result.state, "finished")
            self.assertEqual(len(send_client.calls), 1)
            self.assertEqual(bundle.tool_definitions[0]["function"]["name"], GMAIL_SEND_TOOL_NAME)
            self.assertIsNotNone(bundle.reconciliation_service.registry.resolve(GMAIL_SEND_TOOL_NAME))

    def test_reconciliation_registration_uses_exact_same_tool_name(self):
        registration = build_gmail_send_reconciliation_registration(
            client=StubReadClient(), message_id_domain="mail.example.com",
        )
        self.assertEqual(registration["tool_name"], GMAIL_SEND_TOOL_NAME)
        self.assertIsInstance(registration["adapter"], GmailSendReconciliationAdapter)
        self.assertIsInstance(registration["registry"], ToolActionReconciliationRegistry)
        self.assertIs(registration["registry"].resolve(GMAIL_SEND_TOOL_NAME),
                      registration["adapter"])


if __name__ == "__main__":
    unittest.main()
