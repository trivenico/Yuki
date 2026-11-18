import unittest

from core.conversation_application_runtime import ConversationApplicationRuntime
from ui.desktop.authenticated_conversation import AuthenticatedDesktopConversation
from ui.desktop.conversation_adapter import DesktopConversationAdapter


class FakeStore:
    def get_conversation(self, *, owner_id, conversation_id):
        return {"conversation_id": conversation_id, "version": 0}


class FakeConversations:
    def __init__(self):
        self.last_reply = None

    def create(self, *, owner_id, title):
        return {"conversation_id": "conversation-1", "title": title, "version": 0}

    def reply(self, **kwargs):
        self.last_reply = kwargs
        return {"status": "completed", "answer": "Yuki reply"}


class FakeMemory:
    context_enabled = False

    def context_entries(self, *, query, limit):
        return []


class FakeLearning:
    def __init__(self, patch):
        self.patch = patch
        self.calls = []
        self.closed = False

    def close(self):
        self.closed = True

    def build_few_shot_patch(self, query, *, max_examples, max_chars):
        self.calls.append((query, max_examples, max_chars))
        return self.patch


def _adapter(*, memory=None, learning=None):
    conversations = FakeConversations()
    application = ConversationApplicationRuntime(
        conversations=conversations, store=FakeStore(), owner_id="owner-1",
    )
    return DesktopConversationAdapter(
        application, owner_id="owner-1", memory=memory, learning=learning,
    ), conversations


class DesktopLearningContextTests(unittest.TestCase):
    def test_no_learning_dependency_preserves_existing_chat_behavior(self):
        adapter, conversations = _adapter()
        result = adapter.send("Hello Yuki")
        self.assertEqual(result["answer"], "Yuki reply")
        self.assertIsNone(conversations.last_reply["system_text"])

    def test_approved_few_shot_patch_is_added_to_model_context(self):
        patch = "Approved historical examples (untrusted reference data)."
        learning = FakeLearning(patch)
        adapter, conversations = _adapter(memory=FakeMemory(), learning=learning)
        adapter.send("How should I test this Python function?")
        self.assertEqual(conversations.last_reply["system_text"], patch)
        self.assertEqual(
            learning.calls,
            [("How should I test this Python function?", 3, 4_000)],
        )

    def test_memory_and_few_shot_context_are_composed(self):
        class EnabledMemory:
            context_enabled = True

            def context_entries(self, *, query, limit):
                return [{"category": "preference", "text": "Prefer concise answers."}]

        learning = FakeLearning("Approved historical examples.")
        adapter, conversations = _adapter(memory=EnabledMemory(), learning=learning)
        adapter.send("Explain a Python test.")
        context = conversations.last_reply["system_text"]
        self.assertIn("Prefer concise answers.", context)
        self.assertIn("Approved historical examples.", context)
        self.assertIn("\n\n", context)

    def test_invalid_learning_context_fails_before_model_request(self):
        learning = FakeLearning("x" * 4_001)
        adapter, conversations = _adapter(learning=learning)
        with self.assertRaisesRegex(RuntimeError, "invalid context"):
            adapter.send("Hello")
        self.assertIsNone(conversations.last_reply)

    def test_adapter_close_invalidates_learning_coordinator_reference(self):
        learning = FakeLearning("Approved historical examples.")
        adapter, _ = _adapter(learning=learning)
        adapter.close()
        self.assertTrue(learning.closed)
        self.assertIsNone(adapter.conversation_id)
        self.assertIsNone(adapter._learning)

    def test_authenticated_session_invalidation_closes_learning_context(self):
        class Identity:
            actor_id = "owner-1"

        class Sessions:
            def __init__(self, identity):
                self.identity = identity

            def current_identity(self):
                return self.identity

        identity = Identity()
        sessions = Sessions(identity)
        learning = FakeLearning("Approved historical examples.")
        conversations = FakeConversations()
        application = ConversationApplicationRuntime(
            conversations=conversations, store=FakeStore(), owner_id="owner-1",
        )
        facade = AuthenticatedDesktopConversation(
            application, identity=identity, sessions=sessions, learning=learning,
        )
        facade.invalidate()
        self.assertTrue(learning.closed)
        with self.assertRaises(PermissionError):
            facade.send("This must not be sent after logout.")

    def test_learning_dependency_must_expose_patch_builder(self):
        conversations = FakeConversations()
        application = ConversationApplicationRuntime(
            conversations=conversations, store=FakeStore(), owner_id="owner-1",
        )
        with self.assertRaisesRegex(TypeError, "build_few_shot_patch"):
            DesktopConversationAdapter(application, owner_id="owner-1", learning=object())


if __name__ == "__main__":
    unittest.main()
