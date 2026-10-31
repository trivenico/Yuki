import tempfile
import unittest
from pathlib import Path

from core.conversation_store import ConversationStore


class ConversationStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "conversations.sqlite3"
        self.store = ConversationStore(self.path)

    def test_session_and_messages_survive_reopening(self):
        session = self.store.create_session("workspace-a", "Planning")
        first = self.store.append_message("workspace-a", session["id"], "user", "Build Yuki")
        second = self.store.append_message("workspace-a", session["id"], "assistant", "Let's plan")
        reopened = ConversationStore(self.path)
        self.assertEqual(reopened.get_session("workspace-a", session["id"])["title"], "Planning")
        messages = reopened.list_messages("workspace-a", session["id"])
        self.assertEqual([m["id"] for m in messages], [first["id"], second["id"]])
        self.assertEqual([m["role"] for m in messages], ["user", "assistant"])

    def test_workspace_scope_prevents_cross_workspace_reads_and_writes(self):
        session = self.store.create_session("workspace-a")
        with self.assertRaisesRegex(KeyError, "this workspace"):
            self.store.get_session("workspace-b", session["id"])
        with self.assertRaisesRegex(KeyError, "this workspace"):
            self.store.append_message("workspace-b", session["id"], "user", "intrusion")
        self.assertEqual(self.store.list_messages("workspace-a", session["id"]), [])

    def test_idempotent_append_returns_original_and_rejects_conflict(self):
        session = self.store.create_session("workspace-a")
        first = self.store.append_message("workspace-a", session["id"], "user", "hello", idempotency_key="client-1")
        again = self.store.append_message("workspace-a", session["id"], "user", "hello", idempotency_key="client-1")
        self.assertEqual(first, again)
        self.assertEqual(len(self.store.list_messages("workspace-a", session["id"])), 1)
        with self.assertRaisesRegex(ValueError, "different message"):
            self.store.append_message("workspace-a", session["id"], "user", "changed", idempotency_key="client-1")

    def test_archived_sessions_are_read_only_and_hidden_by_default(self):
        session = self.store.create_session("workspace-a")
        archived = self.store.archive_session("workspace-a", session["id"])
        self.assertEqual(archived["status"], "archived")
        self.assertEqual(self.store.list_sessions("workspace-a"), [])
        self.assertEqual(len(self.store.list_sessions("workspace-a", include_archived=True)), 1)
        with self.assertRaisesRegex(ValueError, "read-only"):
            self.store.append_message("workspace-a", session["id"], "user", "no")

    def test_sequence_cursor_and_bounded_limits(self):
        session = self.store.create_session("workspace-a")
        first = self.store.append_message("workspace-a", session["id"], "user", "one")
        self.store.append_message("workspace-a", session["id"], "assistant", "two")
        self.assertEqual([m["content"] for m in self.store.list_messages(
            "workspace-a", session["id"], after_sequence=first["sequence"]
        )], ["two"])
        with self.assertRaisesRegex(ValueError, "between 1 and 500"):
            self.store.list_messages("workspace-a", session["id"], limit=0)

    def test_rejects_invalid_roles_content_and_non_json_metadata(self):
        session = self.store.create_session("workspace-a")
        for role in ("system", "developer", "tool_call", ""):
            with self.subTest(role=role), self.assertRaises(ValueError):
                self.store.append_message("workspace-a", session["id"], role, "content")
        with self.assertRaisesRegex(ValueError, "non-empty"):
            self.store.append_message("workspace-a", session["id"], "user", " ")
        with self.assertRaisesRegex(ValueError, "JSON-compatible"):
            self.store.append_message("workspace-a", session["id"], "user", "x", metadata={"bad": object()})

    def test_turn_lifecycle_is_atomic_and_prevents_concurrent_session_turns(self):
        session = self.store.create_session("workspace-a")
        first = self.store.begin_turn("workspace-a", session["id"], "request-1", "first question")
        self.assertEqual(first["status"], "running")
        with self.assertRaisesRegex(RuntimeError, "active turn"):
            self.store.begin_turn("workspace-a", session["id"], "request-2", "overlapping question")
        answer = self.store.complete_turn("workspace-a", "request-1", "first answer")
        self.assertEqual(answer["role"], "assistant")
        self.assertEqual(self.store.list_turns("workspace-a", session["id"])[0]["status"], "completed")
        second = self.store.begin_turn("workspace-a", session["id"], "request-2", "next question")
        self.assertEqual(second["status"], "running")

    def test_turn_idempotency_and_workspace_isolation(self):
        session = self.store.create_session("workspace-a")
        first = self.store.begin_turn("workspace-a", session["id"], "stable-request", "hello")
        again = self.store.begin_turn("workspace-a", session["id"], "stable-request", "hello")
        self.assertEqual(first["user_message_id"], again["user_message_id"])
        with self.assertRaisesRegex(ValueError, "different user content"):
            self.store.begin_turn("workspace-a", session["id"], "stable-request", "changed")
        with self.assertRaisesRegex(KeyError, "this workspace"):
            self.store.list_turns("workspace-b", session["id"])

    def test_failed_turn_keeps_user_message_and_allows_retry(self):
        session = self.store.create_session("workspace-a")
        self.store.begin_turn("workspace-a", session["id"], "failed-request", "retry me")
        failed = self.store.fail_turn("workspace-a", "failed-request", error_code="provider_error")
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(failed["error_code"], "provider_error")
        self.assertEqual([m["role"] for m in self.store.list_messages("workspace-a", session["id"])], ["user"])
        retried = self.store.begin_turn("workspace-a", session["id"], "retry-request", "retry me")
        self.assertEqual(retried["status"], "running")

    def test_requires_persistent_database_and_valid_workspace(self):
        with self.assertRaisesRegex(ValueError, "persistent database"):
            ConversationStore(":memory:")
        with self.assertRaisesRegex(ValueError, "workspace_id"):
            self.store.create_session(" ")


if __name__ == "__main__":
    unittest.main()
