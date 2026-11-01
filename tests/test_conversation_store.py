import tempfile
import unittest
from pathlib import Path

from companion.conversation_store import ConversationStore, ConversationStoreError


class ConversationStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = ConversationStore(Path(self.temp_dir.name) / ".yuki" / "conversations.sqlite3")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_owner_scoped_create_append_list_and_read(self):
        created = self.store.create(owner_id="alice", title="Project discussion")
        self.assertEqual(created.version, 0)
        saved = self.store.append_turn(
            owner_id="alice", conversation_id=created.conversation_id,
            user_message="Plan the work", assistant_message="Let's review the plan.",
            expected_version=0,
        )
        self.assertEqual(saved.version, 1)
        self.assertEqual([m["role"] for m in saved.messages], ["user", "assistant"])
        self.assertEqual(self.store.list(owner_id="alice")[0]["conversation_id"], created.conversation_id)
        with self.assertRaises(KeyError):
            self.store.get(owner_id="bob", conversation_id=created.conversation_id)

    def test_optimistic_version_prevents_lost_updates(self):
        created = self.store.create(owner_id="alice")
        self.store.append_turn(
            owner_id="alice", conversation_id=created.conversation_id,
            user_message="first", assistant_message="reply", expected_version=0,
        )
        with self.assertRaises(ConversationStoreError):
            self.store.append_turn(
                owner_id="alice", conversation_id=created.conversation_id,
                user_message="stale", assistant_message="stale reply", expected_version=0,
            )

    def test_delete_cascades_messages_and_is_owner_scoped(self):
        created = self.store.create(owner_id="alice")
        self.store.append_turn(
            owner_id="alice", conversation_id=created.conversation_id,
            user_message="private", assistant_message="reply", expected_version=0,
        )
        self.assertFalse(self.store.delete(owner_id="bob", conversation_id=created.conversation_id))
        self.assertTrue(self.store.delete(owner_id="alice", conversation_id=created.conversation_id))
        with self.assertRaises(KeyError):
            self.store.get(owner_id="alice", conversation_id=created.conversation_id)

    def test_message_and_list_limits_are_enforced(self):
        created = self.store.create(owner_id="alice")
        with self.assertRaises(ValueError):
            self.store.append_turn(
                owner_id="alice", conversation_id=created.conversation_id,
                user_message="x" * 8001, assistant_message="ok", expected_version=0,
            )
        with self.assertRaises(ValueError):
            self.store.list(owner_id="alice", limit=101)


if __name__ == "__main__":
    unittest.main()
