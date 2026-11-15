import json
import tempfile
import unittest
from pathlib import Path

from core.google_gmail_send_tool import GmailSendTool
from core.tool_action_context import ToolActionContext
from core.tool_action_approvals import SQLiteToolActionApprovalStore
from core.pending_tool_action_proposals import SQLitePendingToolActionProposalStore
from core.tool_action_journal import ToolActionJournal
from core.approved_tool_action_executor import ApprovedToolActionExecutor
from core.guarded_tool_runtime import GuardedToolRuntime
from core.tool_capabilities import ToolCapabilityCatalog
from core.google_gmail_send_tool import gmail_send_tool_capability
from core.google_gmail_send_client import GmailSendClient


class StubGmailClient(GmailSendClient):
    def __init__(self):
        self.calls = []
    def send_email(self, **kwargs):
        self.calls.append(kwargs)
        return {"provider_message_id": "message123", "thread_id": None,
                "message_id": "<yuki-" + kwargs["action"].action_id + "@mail.example.com>"}


class GmailSendToolTests(unittest.TestCase):
    def setUp(self):
        self.client = StubGmailClient()
        self.tool = GmailSendTool(self.client)
        self.arguments = {
            "account_id": "google-account-1",
            "to": "recipient@example.com",
            "subject": "Hello",
            "body": "A message",
        }
        self.context = ToolActionContext.create(
            "owner-1", "gmail.send_email", self.arguments,
        )

    def test_schema_argument_bounds_fit_structured_tool_protocol_budget(self):
        schema = GmailSendTool.definition()["function"]["parameters"]["properties"]
        self.assertEqual(schema["body"]["maxLength"], 4000)
        self.assertEqual(schema["to"]["oneOf"][1]["maxItems"], 5)
        self.assertEqual(schema["to"]["oneOf"][0]["maxLength"], 320)
        self.assertEqual(schema["to"]["oneOf"][1]["items"]["maxLength"], 320)

    def test_direct_invocation_without_approval_context_is_denied(self):
        with self.assertRaises(PermissionError):
            self.tool(json.dumps(self.arguments))

    def test_invocation_uses_authenticated_owner_and_host_action_identity(self):
        result = json.loads(self.tool.invoke_action(
            json.dumps(self.arguments), action_context=self.context,
            principal_id="owner-1",
        ))
        self.assertEqual(result["provider_message_id"], "message123")
        self.assertEqual(len(self.client.calls), 1)
        call = self.client.calls[0]
        self.assertEqual(call["owner_id"], "owner-1")
        self.assertEqual(call["account_id"], "google-account-1")
        self.assertEqual(call["action"].action_id, self.context.action_id)
        self.assertEqual(call["action"].tool_name, "gmail.send_email")

    def test_rejects_owner_tool_or_argument_substitution(self):
        with self.assertRaises(PermissionError):
            self.tool.invoke_action(json.dumps(self.arguments),
                action_context=self.context, principal_id="owner-2")
        with self.assertRaises(PermissionError):
            self.tool.invoke_action(json.dumps(self.arguments),
                action_context=ToolActionContext.create("owner-1", "other.tool", self.arguments),
                principal_id="owner-1")
        changed = dict(self.arguments, to="attacker@example.com")
        with self.assertRaises(ValueError):
            self.context.verify("owner-1", "gmail.send_email", changed)
        self.assertEqual(self.client.calls, [])

    def test_only_approved_proposal_reaches_action_aware_handler_and_journal(self):
        class AllowPolicy:
            def authorize_tool(self, name):
                return name == "gmail.send_email"

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            proposals = SQLitePendingToolActionProposalStore(str(root / "proposals.sqlite"))
            approvals = SQLiteToolActionApprovalStore(str(root / "approvals.sqlite"))
            journal = ToolActionJournal(str(root / "journal.sqlite"))
            runtime = GuardedToolRuntime(
                {"gmail.send_email": self.tool}, AllowPolicy(),
                capabilities=ToolCapabilityCatalog({
                    "gmail.send_email": gmail_send_tool_capability(),
                }),
                require_action_context=True, action_journal=journal,
                approval_store=approvals,
            )
            executor = ApprovedToolActionExecutor(proposals, approvals, runtime)
            context = ToolActionContext.create("owner-1", "gmail.send_email", self.arguments)
            proposal = proposals.create(context, self.arguments, principal_id="owner-1")
            with self.assertRaisesRegex(ValueError, "not been approved"):
                executor.execute(proposal.proposal_id, principal_id="owner-1")
            self.assertEqual(self.client.calls, [])
            proposals.decide(proposal.proposal_id, principal_id="owner-1", decision="approved")
            result = executor.execute(proposal.proposal_id, principal_id="owner-1")
            self.assertEqual(result.state, "finished")
            self.assertEqual(len(self.client.calls), 1)
            action = journal.get(context.idempotency_key)
            self.assertEqual(action.state, "succeeded")
            self.assertEqual(action.action_id, context.action_id)
            with self.assertRaisesRegex(ValueError, "already claimed"):
                executor.execute(proposal.proposal_id, principal_id="owner-1")
            self.assertEqual(len(self.client.calls), 1)
    def test_model_schema_is_strict_and_requires_every_argument(self):
        schema = self.tool.definition()["function"]
        self.assertEqual(schema["name"], "gmail.send_email")
        self.assertEqual(set(schema["parameters"]["required"]),
                         {"account_id", "to", "subject", "body"})
        self.assertFalse(schema["parameters"]["additionalProperties"])


if __name__ == "__main__":
    unittest.main()
