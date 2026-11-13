import tempfile
import unittest
from pathlib import Path

from core.conversation_store import (
    ConversationConflict,
    ConversationNotFound,
    ConversationStore,
)


class ConversationStoreTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.store = ConversationStore(Path(self.tempdir.name) / "conversations.sqlite3")
        self.owner = "user-123"
        self.other_owner = "user-456"
        self.conversation = self.store.create_conversation(owner_id=self.owner)

    def tearDown(self):
        self.tempdir.cleanup()

    def append(self, role, content, key, version):
        return self.store.append_message(
            owner_id=self.owner,
            conversation_id=self.conversation["conversation_id"],
            role=role,
            content=content,
            idempotency_key=key,
            expected_version=version,
        )

    def test_create_append_and_list_preserve_sequence_and_version(self):
        user = self.append("user", "Hello Yuki", "user-1", 0)
        assistant = self.append("assistant", "Hello. How can I help?", "assistant-1", 1)
        self.assertEqual(user["version"], 1)
        self.assertEqual(assistant["version"], 2)
        messages = self.store.list_messages(
            owner_id=self.owner, conversation_id=self.conversation["conversation_id"]
        )
        self.assertEqual([m["role"] for m in messages], ["user", "assistant"])
        self.assertEqual([m["sequence"] for m in messages], [1, 2])

    def test_owner_scope_hides_conversation_and_messages(self):
        conversation_id = self.conversation["conversation_id"]
        with self.assertRaises(ConversationNotFound):
            self.store.get_conversation(owner_id=self.other_owner, conversation_id=conversation_id)
        with self.assertRaises(ConversationNotFound):
            self.store.list_messages(owner_id=self.other_owner, conversation_id=conversation_id)
        with self.assertRaises(ConversationNotFound):
            self.store.append_message(
                owner_id=self.other_owner, conversation_id=conversation_id,
                role="user", content="intrude", idempotency_key="x",
            )

    def test_optimistic_version_rejects_stale_writer(self):
        self.append("user", "First", "first", 0)
        with self.assertRaises(ConversationConflict):
            self.append("user", "Stale", "stale", 0)
        self.assertEqual(len(self.store.list_messages(
            owner_id=self.owner, conversation_id=self.conversation["conversation_id"]
        )), 1)

    def test_idempotent_replay_returns_existing_message(self):
        first = self.append("user", "Retry safely", "request-1", 0)
        replay = self.append("user", "Retry safely", "request-1", 0)
        self.assertTrue(first["created"])
        self.assertFalse(replay["created"])
        self.assertEqual(first["message"]["message_id"], replay["message"]["message_id"])
        with self.assertRaises(ConversationConflict):
            self.append("user", "Different payload", "request-1", 1)

    def test_conversation_history_is_paginated(self):
        for i in range(5):
            self.append("user", f"message {i}", f"key-{i}", i)
        latest = self.store.list_messages(
            owner_id=self.owner, conversation_id=self.conversation["conversation_id"], limit=2
        )
        after = self.store.list_messages(
            owner_id=self.owner, conversation_id=self.conversation["conversation_id"],
            after_sequence=3, limit=2,
        )
        self.assertEqual([m["sequence"] for m in latest], [4, 5])
        self.assertEqual([m["sequence"] for m in after], [4, 5])

    def test_archive_hides_from_default_list_and_prevents_writes(self):
        self.store.archive_conversation(
            owner_id=self.owner, conversation_id=self.conversation["conversation_id"]
        )
        self.assertEqual(self.store.list_conversations(owner_id=self.owner), [])
        self.assertEqual(len(self.store.list_conversations(
            owner_id=self.owner, include_archived=True
        )), 1)
        with self.assertRaises(ConversationConflict):
            self.append("user", "No longer writable", "after-archive", 0)

    def test_rejects_invalid_roles_content_and_versions(self):
        conversation_id = self.conversation["conversation_id"]
        for role, content, version in (
            ([], "x", 0), ("system", "x", 0), ("user", "", 0),
            ("user", "x", True), ("assistant", "x" * 24_001, 0),
        ):
            with self.subTest(role=role, version=version), self.assertRaises(ValueError):
                self.store.append_message(
                    owner_id=self.owner, conversation_id=conversation_id,
                    role=role, content=content, idempotency_key="invalid-" + str(version),
                    expected_version=version,
                )

    def test_title_is_derived_from_first_user_message(self):
        result = self.append("user", "  A concise   title  ", "title", 0)
        conversation = self.store.get_conversation(
            owner_id=self.owner, conversation_id=self.conversation["conversation_id"]
        )
        self.assertEqual(conversation["title"], "A concise title")

    def test_list_conversations_is_owner_scoped_and_limited(self):
        self.store.create_conversation(owner_id=self.other_owner, title="Private")
        self.assertEqual(len(self.store.list_conversations(owner_id=self.owner)), 1)
        with self.assertRaises(ValueError):
            self.store.list_conversations(owner_id=self.owner, limit=101)



    def test_user_message_and_generation_run_are_created_atomically(self):
        result = self.store.append_message(
            owner_id=self.owner, conversation_id=self.conversation["conversation_id"],
            role="user", content="Recover me", idempotency_key="recover-1",
            expected_version=0, generation_request_id="recover-1",
        )
        self.assertTrue(result["created"])
        pending = self.store.list_interrupted_generations(
            owner_id=self.owner, conversation_id=self.conversation["conversation_id"]
        )
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["request_id"], "recover-1")
        self.assertEqual(pending[0]["status"], "running")

    def test_generation_checkpoint_is_bounded_and_terminal_state_is_immutable(self):
        self.store.append_message(
            owner_id=self.owner, conversation_id=self.conversation["conversation_id"],
            role="user", content="Checkpoint me", idempotency_key="checkpoint-1",
            expected_version=0, generation_request_id="checkpoint-1",
        )
        self.assertTrue(self.store.update_generation_checkpoint(
            owner_id=self.owner, conversation_id=self.conversation["conversation_id"],
            request_id="checkpoint-1", checkpoint={"phase": "model_running", "turn": 1},
        ))
        self.assertTrue(self.store.finish_generation(
            owner_id=self.owner, conversation_id=self.conversation["conversation_id"],
            request_id="checkpoint-1", status="interrupted",
        ))
        self.assertFalse(self.store.finish_generation(
            owner_id=self.owner, conversation_id=self.conversation["conversation_id"],
            request_id="checkpoint-1", status="completed",
        ))
        self.assertEqual(self.store.list_interrupted_generations(
            owner_id=self.owner, conversation_id=self.conversation["conversation_id"]
        ), [])
        with self.assertRaises(ValueError):
            self.store.update_generation_checkpoint(
                owner_id=self.owner, conversation_id=self.conversation["conversation_id"],
                request_id="checkpoint-1", checkpoint={"payload": "x" * 5000},
            )

    def test_generation_records_are_owner_scoped(self):
        self.store.append_message(
            owner_id=self.owner, conversation_id=self.conversation["conversation_id"],
            role="user", content="Private generation", idempotency_key="private-1",
            expected_version=0, generation_request_id="private-1",
        )
        self.assertEqual(self.store.list_interrupted_generations(
            owner_id=self.other_owner, conversation_id=self.conversation["conversation_id"]
        ), [])

    def test_generation_lease_is_exclusive_across_independent_store_instances(self):
        second = ConversationStore(Path(self.tempdir.name) / "conversations.sqlite3")
        conversation_id = self.conversation["conversation_id"]
        try:
            first_lease = self.store.acquire_generation_lease(
                owner_id=self.owner, conversation_id=conversation_id,
                lease_token="worker-one-token", ttl_seconds=30,
            )
            self.assertGreater(first_lease["expires_at"], 0)
            with self.assertRaises(ConversationConflict):
                second.acquire_generation_lease(
                    owner_id=self.owner, conversation_id=conversation_id,
                    lease_token="worker-two-token", ttl_seconds=30,
                )
            self.assertTrue(self.store.release_generation_lease(
                owner_id=self.owner, conversation_id=conversation_id,
                lease_token="worker-one-token",
            ))
            second_lease = second.acquire_generation_lease(
                owner_id=self.owner, conversation_id=conversation_id,
                lease_token="worker-two-token", ttl_seconds=30,
            )
            self.assertGreater(second_lease["expires_at"], first_lease["expires_at"])
        finally:

    def test_expired_lease_can_be_reclaimed_but_old_token_cannot_release_new_lease(self):
        second = ConversationStore(Path(self.tempdir.name) / "conversations.sqlite3")
        conversation_id = self.conversation["conversation_id"]
        try:
            self.store.acquire_generation_lease(
                owner_id=self.owner, conversation_id=conversation_id,
                lease_token="stale-worker", ttl_seconds=30,
            )
            with self.store._connect() as db:
                db.execute(
                    "UPDATE conversation_generation_leases SET expires_at=? WHERE conversation_id=?",
                    (0, conversation_id),
                )
            second.acquire_generation_lease(
                owner_id=self.owner, conversation_id=conversation_id,
                lease_token="replacement-worker", ttl_seconds=30,
            )
            self.assertFalse(self.store.release_generation_lease(
                owner_id=self.owner, conversation_id=conversation_id,
                lease_token="stale-worker",
            ))
            self.assertTrue(second.release_generation_lease(
                owner_id=self.owner, conversation_id=conversation_id,
                lease_token="replacement-worker",
            ))
        finally:

if __name__ == "__main__":
    unittest.main()
