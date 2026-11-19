"""Cross-account isolation tests for durable desktop conversation storage."""
import tempfile
import unittest
from pathlib import Path

from core.conversation_store import ConversationNotFound, ConversationStore


class ConversationOwnerIsolationTests(unittest.TestCase):
    def test_accounts_cannot_enumerate_select_or_read_each_others_conversations(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ConversationStore(Path(directory) / "shared.sqlite3")
            alice = store.create_conversation(owner_id="account-alice", title="Alice private")
            bob = store.create_conversation(owner_id="account-bob", title="Bob private")

            store.append_message(
                owner_id="account-alice",
                conversation_id=alice["conversation_id"],
                role="user",
                content="Alice private message",
                idempotency_key="alice-message-1",
                expected_version=0,
            )
            store.append_message(
                owner_id="account-bob",
                conversation_id=bob["conversation_id"],
                role="user",
                content="Bob private message",
                idempotency_key="bob-message-1",
                expected_version=0,
            )

            alice_ids = {
                item["conversation_id"]
                for item in store.list_conversations(owner_id="account-alice")
            }
            bob_ids = {
                item["conversation_id"]
                for item in store.list_conversations(owner_id="account-bob")
            }
            self.assertEqual(alice_ids, {alice["conversation_id"]})
            self.assertEqual(bob_ids, {bob["conversation_id"]})

            with self.assertRaises(ConversationNotFound):
                store.get_conversation(
                    owner_id="account-alice",
                    conversation_id=bob["conversation_id"],
                )
            with self.assertRaises(ConversationNotFound):
                store.list_messages(
                    owner_id="account-alice",
                    conversation_id=bob["conversation_id"],
                )

            alice_history = store.list_messages(
                owner_id="account-alice",
                conversation_id=alice["conversation_id"],
            )
            bob_history = store.list_messages(
                owner_id="account-bob",
                conversation_id=bob["conversation_id"],
            )
            self.assertEqual([item["content"] for item in alice_history], ["Alice private message"])
            self.assertEqual([item["content"] for item in bob_history], ["Bob private message"])

    def test_unknown_account_sees_empty_history_not_other_tenants(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ConversationStore(Path(directory) / "shared.sqlite3")
            store.create_conversation(owner_id="account-alice", title="Alice private")
            self.assertEqual(
                store.list_conversations(owner_id="account-charlie"),
                [],
            )


if __name__ == "__main__":
    unittest.main()
