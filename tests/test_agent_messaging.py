import tempfile
import unittest
from pathlib import Path

from automation.agent_messaging import AgentMailbox


class AgentMailboxTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.grants = {"agent:message:send", "agent:message:read", "agent:message:ack"}
        self.mailbox = AgentMailbox(
            str(Path(self.tmp.name) / "yuki.sqlite3"),
            authorize=lambda actor, action, resource: actor == resource and action in self.grants,
        )

    def test_send_inbox_acknowledge_and_persistence(self):
        sent = self.mailbox.send(
            actor_id="research", sender_agent_id="research", recipient_agent_id="writer",
            body="Summarize the verified sources.", idempotency_key="msg-1",
        )
        restarted = AgentMailbox(
            self.mailbox.database_path,
            authorize=lambda actor, action, resource: actor == resource and action in self.grants,
        )
        inbox = restarted.inbox(actor_id="writer", recipient_agent_id="writer")
        self.assertEqual([item.message_id for item in inbox], [sent.message_id])
        self.assertEqual(inbox[0].body, "Summarize the verified sources.")
        ack = restarted.acknowledge(
            actor_id="writer", recipient_agent_id="writer", message_id=sent.message_id,
        )
        self.assertIsNotNone(ack.acknowledged_at)
        self.assertEqual(restarted.inbox(actor_id="writer", recipient_agent_id="writer"), [])

    def test_send_is_idempotent_and_rejects_payload_mismatch(self):
        args = dict(actor_id="research", sender_agent_id="research",
                    recipient_agent_id="writer", body="Evidence", idempotency_key="same")
        first = self.mailbox.send(**args)
        second = self.mailbox.send(**args)
        self.assertEqual(first.message_id, second.message_id)
        with self.assertRaises(ValueError):
            self.mailbox.send(**{**args, "body": "Different evidence"})

    def test_inbox_and_ack_are_recipient_scoped(self):
        sent = self.mailbox.send(
            actor_id="research", sender_agent_id="research", recipient_agent_id="writer",
            body="Private to writer", idempotency_key="private",
        )
        with self.assertRaises(PermissionError):
            self.mailbox.inbox(actor_id="intruder", recipient_agent_id="writer")
        with self.assertRaises(KeyError):
            self.mailbox.acknowledge(
                actor_id="research", recipient_agent_id="research", message_id=sent.message_id,
            )

    def test_limits_and_invalid_inputs_are_rejected(self):
        with self.assertRaises(ValueError):
            self.mailbox.inbox(actor_id="writer", recipient_agent_id="writer", limit=101)
        with self.assertRaises(ValueError):
            self.mailbox.send(
                actor_id="research", sender_agent_id="research", recipient_agent_id="writer",
                body="x" * 8001, idempotency_key="oversized",
            )
