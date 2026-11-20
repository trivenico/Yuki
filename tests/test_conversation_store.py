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


    def test_generation_lease_rejects_concurrent_claim_and_releases_by_token(self):
        conversation_id = self.conversation["conversation_id"]
        first = self.store.acquire_generation_lease(
            owner_id=self.owner, conversation_id=conversation_id,
            lease_token="worker-one", ttl_seconds=30,
        )
        self.assertGreater(first["expires_at"], 0)
        with self.assertRaises(ConversationConflict):
            self.store.acquire_generation_lease(
                owner_id=self.owner, conversation_id=conversation_id,
                lease_token="worker-two", ttl_seconds=30,
            )
        self.assertFalse(self.store.release_generation_lease(
            owner_id=self.owner, conversation_id=conversation_id,
            lease_token="worker-two",
        ))
        self.assertTrue(self.store.release_generation_lease(
            owner_id=self.owner, conversation_id=conversation_id,
            lease_token="worker-one",
        ))
        self.assertFalse(self.store.release_generation_lease(
            owner_id=self.owner, conversation_id=conversation_id,
            lease_token="worker-one",
        ))

    def test_expired_lease_can_be_reclaimed_without_old_token_releasing_new_lease(self):
        from unittest.mock import patch

        conversation_id = self.conversation["conversation_id"]
        with patch("core.conversation_store.time.time", return_value=100.0):
            self.store.acquire_generation_lease(
                owner_id=self.owner, conversation_id=conversation_id,
                lease_token="stale-worker", ttl_seconds=5,
            )
        with patch("core.conversation_store.time.time", return_value=106.0):
            replacement = self.store.acquire_generation_lease(
                owner_id=self.owner, conversation_id=conversation_id,
                lease_token="replacement-worker", ttl_seconds=30,
            )
            self.assertEqual(replacement["expires_at"], 136.0)
            self.assertFalse(self.store.release_generation_lease(
                owner_id=self.owner, conversation_id=conversation_id,
                lease_token="stale-worker",
            ))
            self.assertTrue(self.store.release_generation_lease(
                owner_id=self.owner, conversation_id=conversation_id,
                lease_token="replacement-worker",
            ))


    def test_live_generation_lease_can_be_renewed_by_its_owner_token(self):
        from unittest.mock import patch

        conversation_id = self.conversation["conversation_id"]
        with patch("core.conversation_store.time.time", return_value=200.0):
            initial = self.store.acquire_generation_lease(
                owner_id=self.owner, conversation_id=conversation_id,
                lease_token="active-worker", ttl_seconds=10,
            )
        with patch("core.conversation_store.time.time", return_value=205.0):
            renewed = self.store.renew_generation_lease(
                owner_id=self.owner, conversation_id=conversation_id,
                lease_token="active-worker", ttl_seconds=30,
            )
        self.assertEqual(initial["expires_at"], 210.0)
        self.assertEqual(renewed["expires_at"], 235.0)

    def test_expired_or_superseded_worker_cannot_renew_generation_lease(self):
        from unittest.mock import patch

        conversation_id = self.conversation["conversation_id"]
        with patch("core.conversation_store.time.time", return_value=300.0):
            self.store.acquire_generation_lease(
                owner_id=self.owner, conversation_id=conversation_id,
                lease_token="old-worker", ttl_seconds=5,
            )
        with patch("core.conversation_store.time.time", return_value=306.0):
            self.assertIsNone(self.store.renew_generation_lease(
                owner_id=self.owner, conversation_id=conversation_id,
                lease_token="old-worker", ttl_seconds=30,
            ))
            self.store.acquire_generation_lease(
                owner_id=self.owner, conversation_id=conversation_id,
                lease_token="new-worker", ttl_seconds=30,
            )
            self.assertIsNone(self.store.renew_generation_lease(
                owner_id=self.owner, conversation_id=conversation_id,
                lease_token="old-worker", ttl_seconds=60,
            ))
            self.assertEqual(
                self.store.renew_generation_lease(
                    owner_id=self.owner, conversation_id=conversation_id,
                    lease_token="new-worker", ttl_seconds=60,
                )["expires_at"],
                366.0,
            )


    def test_assistant_persistence_requires_current_live_lease_when_fenced(self):
        from unittest.mock import patch

        conversation_id = self.conversation["conversation_id"]
        with patch("core.conversation_store.time.time", return_value=400.0):
            self.store.acquire_generation_lease(
                owner_id=self.owner, conversation_id=conversation_id,
                lease_token="current-worker", ttl_seconds=10,
            )
            with self.assertRaisesRegex(ConversationConflict, "lease is no longer owned"):
                self.store.append_message(
                    owner_id=self.owner, conversation_id=conversation_id,
                    role="assistant", content="Stale response",
                    idempotency_key="assistant:stale", expected_version=0,
                    lease_token="stale-worker",
                )
            accepted = self.store.append_message(
                owner_id=self.owner, conversation_id=conversation_id,
                role="assistant", content="Current response",
                idempotency_key="assistant:current", expected_version=0,
                lease_token="current-worker",
            )
            self.assertTrue(accepted["created"])
            self.assertEqual(accepted["message"]["content"], "Current response")

    def test_expired_lease_cannot_persist_assistant_even_before_reclaim(self):
        from unittest.mock import patch

        conversation_id = self.conversation["conversation_id"]
        with patch("core.conversation_store.time.time", return_value=500.0):
            self.store.acquire_generation_lease(
                owner_id=self.owner, conversation_id=conversation_id,
                lease_token="expired-worker", ttl_seconds=5,
            )
        with patch("core.conversation_store.time.time", return_value=506.0):
            with self.assertRaisesRegex(ConversationConflict, "lease is no longer owned"):
                self.store.append_message(
                    owner_id=self.owner, conversation_id=conversation_id,
                    role="assistant", content="Expired response",
                    idempotency_key="assistant:expired", expected_version=0,
                    lease_token="expired-worker",
                )

if __name__ == "__main__":
    unittest.main()
