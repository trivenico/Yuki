import tempfile
import unittest
from pathlib import Path

from automation.agent_message_bus import (
    AgentMessageBus,
    AgentMessageConflict,
    AgentMessageError,
)


class AgentMessageBusTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.tmp.name) / "messages.sqlite3")
        self.permissions = set()
        self.bus = AgentMessageBus(
            self.path,
            registered_agent_ids=("research", "engineering", "supervisor"),
            authorize=lambda actor, action, resource: (actor, action, resource) in self.permissions,
        )
        for actor in ("research", "engineering", "supervisor"):
            for action in ("agent:message:send", "agent:message:receive", "agent:message:read"):
                self.permissions.add((actor, action, "research"))
                self.permissions.add((actor, action, "engineering"))
                self.permissions.add((actor, action, "supervisor"))
                self.permissions.add((actor, action, "thread-1"))

    def tearDown(self):
        self.tmp.cleanup()

    def send(self, **kwargs):
        return self.bus.send(
            actor_id="research", sender_id="research", recipient_id="engineering",
            body="Review the API boundary.", idempotency_key="msg-1",
            thread_id="thread-1", **kwargs,
        )

    def test_send_and_inbox_are_durable_and_scoped_to_recipient(self):
        sent = self.send()
        reopened = AgentMessageBus(
            self.path, registered_agent_ids=("research", "engineering", "supervisor"),
            authorize=lambda actor, action, resource: True,
        )
        inbox = reopened.inbox(actor_id="engineering")
        self.assertEqual(inbox, (sent,))
        self.assertEqual(reopened.inbox(actor_id="research"), ())

    def test_idempotent_send_returns_original_message(self):
        first = self.send()
        second = self.send()
        self.assertEqual(first.message_id, second.message_id)

    def test_reused_idempotency_key_with_different_payload_conflicts(self):
        self.send()
        with self.assertRaises(AgentMessageConflict):
            self.bus.send(
                actor_id="research", sender_id="research", recipient_id="engineering",
                body="Different payload", idempotency_key="msg-1", thread_id="thread-1",
            )

    def test_sender_spoofing_is_rejected(self):
        with self.assertRaises(PermissionError):
            self.bus.send(
                actor_id="engineering", sender_id="research", recipient_id="engineering",
                body="spoof", idempotency_key="spoof", thread_id="thread-1",
            )

    def test_unregistered_agent_is_rejected(self):
        with self.assertRaises(AgentMessageError):
            self.bus.send(
                actor_id="research", sender_id="research", recipient_id="unknown",
                body="hello", idempotency_key="unknown", thread_id="thread-1",
            )

    def test_sender_and_recipient_authorization_are_both_required(self):
        self.permissions.clear()
        self.permissions.add(("research", "agent:message:send", "engineering"))
        with self.assertRaises(PermissionError):
            self.send()

    def test_inbox_requires_read_permission(self):
        self.permissions = {p for p in self.permissions if not (p[0] == "engineering" and p[1] == "agent:message:read")}
        self.send()
        with self.assertRaises(PermissionError):
            self.bus.inbox(actor_id="engineering")

    def test_thread_only_returns_participant_messages(self):
        self.send()
        result = self.bus.thread(actor_id="engineering", thread_id="thread-1")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].recipient_id, "engineering")

    def test_message_and_page_limits_are_enforced(self):
        with self.assertRaises(AgentMessageError):
            self.bus.send(
                actor_id="research", sender_id="research", recipient_id="engineering",
                body="x" * 8001, idempotency_key="too-long", thread_id="thread-1",
            )
        with self.assertRaises(ValueError):
            self.bus.inbox(actor_id="engineering", limit=101)

    def test_authorization_callback_failure_fails_closed(self):
        denied = AgentMessageBus(
            self.path, registered_agent_ids=("research", "engineering"),
            authorize=lambda *_: (_ for _ in ()).throw(RuntimeError("ACL failure")),
        )
        with self.assertRaises(PermissionError):
            denied.send(
                actor_id="research", sender_id="research", recipient_id="engineering",
                body="hello", idempotency_key="acl-failure", thread_id="thread-1",
            )


if __name__ == "__main__":
    unittest.main()
