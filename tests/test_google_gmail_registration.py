import unittest

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
        pass
    def send_email(self, **kwargs):
        return {}


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
