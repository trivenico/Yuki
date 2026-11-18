"""Tests for authenticated durable desktop conversation composition."""
from dataclasses import dataclass
import tempfile
import threading
import unittest
from pathlib import Path

from core.conversation_store import ConversationStore
from ui.desktop.shared_conversation_session import build_authenticated_desktop_conversation


@dataclass(frozen=True)
class Identity:
    actor_id: str
    session_id: str = "session-1"


class Sessions:
    def __init__(self, identity):
        self.identity = identity

    def current_identity(self):
        return self.identity


class Provider:
    def __init__(self):
        self.calls = []
        self.close_count = 0

    def generate_chat(self, *, messages, tools, tool_choice):
        self.calls.append((messages, tools, tool_choice))
        return {"text": "Durable desktop reply", "tool_calls": {"tool_calls": []}}

    def close(self):
        self.close_count += 1


class AuthenticatedDesktopCompositionTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.identity = Identity("desktop-owner")
        self.sessions = Sessions(self.identity)
        self.provider = Provider()

    def tearDown(self):
        self.tempdir.cleanup()

    def build(self, *, memory=None, learning=None):
        return build_authenticated_desktop_conversation(
            {"workspace": self.tempdir.name},
            provider=self.provider,
            identity=self.identity,
            sessions=self.sessions,
            memory=memory,
            learning=learning,
            store_factory=lambda config: ConversationStore(
                Path(config["workspace"]) / "conversations.sqlite3"
            ),
        )

    def test_composes_owner_scoped_durable_chat_and_leaves_provider_lifecycle_external(self):
        session = self.build()
        try:
            result = session.conversation.send("Hello", request_id="desktop-1")
            self.assertEqual(result["answer"], "Durable desktop reply")
            self.assertEqual(result["status"], "completed")
            self.assertEqual(self.provider.calls[0][1], [])
            self.assertEqual(self.provider.calls[0][2], "auto")
            self.assertEqual(
                session.conversation.history()[0]["content"],
                "Hello",
            )
            self.assertEqual(
                session.runtime.conversations.list(owner_id="desktop-owner")[0]["conversation_id"],
                result["conversation_id"],
            )
        finally:
            session.close()
        self.assertEqual(self.provider.close_count, 0)
        self.assertTrue(session.runtime.closed)

    def test_failed_runtime_composition_closes_learning_session(self):
        class Learning:
            def __init__(self):
                self.closed = False

            def close(self):
                self.closed = True

        learning = Learning()

        def fail_store(_config):
            raise OSError("store unavailable")

        with self.assertRaisesRegex(OSError, "store unavailable"):
            build_authenticated_desktop_conversation(
                {"workspace": self.tempdir.name},
                provider=self.provider,
                identity=self.identity,
                sessions=self.sessions,
                learning=learning,
                store_factory=fail_store,
            )
        self.assertTrue(learning.closed)

    def test_rejects_identity_not_verified_by_current_session(self):
        self.sessions.identity = None
        with self.assertRaises(PermissionError):
            self.build()
        self.assertEqual(self.provider.calls, [])
        self.assertEqual(self.provider.close_count, 0)

    def test_session_close_invalidates_facade_and_closes_runtime_idempotently(self):
        session = self.build()
        session.conversation.start_new_conversation()
        session.close()
        session.close()
        with self.assertRaises(PermissionError):
            session.conversation.list_conversations()
        self.assertTrue(session.runtime.closed)
        self.assertEqual(self.provider.close_count, 0)


    def test_session_expiry_during_model_response_suppresses_result(self):
        entered_provider = threading.Event()
        release_provider = threading.Event()

        class BlockingProvider(Provider):
            def generate_chat(self, *, messages, tools, tool_choice):
                self.calls.append((messages, tools, tool_choice))
                entered_provider.set()
                if not release_provider.wait(timeout=5):
                    raise TimeoutError("test did not release provider")
                return {"text": "must not reach the expired desktop session", "tool_calls": {"tool_calls": []}}

        self.provider = BlockingProvider()
        session = self.build()
        results = []
        errors = []

        def send():
            try:
                results.append(session.conversation.send("Hello during logout", request_id="desktop-expiry-race"))
            except Exception as exc:
                errors.append(exc)

        worker = threading.Thread(target=send)
        worker.start()
        try:
            self.assertTrue(entered_provider.wait(timeout=2), "provider was not entered")
            # Simulate the session manager revoking the authenticated identity
            # while inference is still running.
            self.sessions.identity = None
        finally:
            release_provider.set()
        worker.join(timeout=5)

        try:
            self.assertFalse(worker.is_alive(), "chat worker did not finish")
            self.assertEqual(results, [], "a response escaped after session expiry")
            self.assertEqual(len(errors), 1)
            self.assertIsInstance(errors[0], PermissionError)
            self.assertIn("expired", str(errors[0]).lower())
        finally:
            session.close()
        self.assertTrue(session.runtime.closed)
        self.assertEqual(self.provider.close_count, 0)


    def test_shutdown_waits_for_active_send_before_closing_runtime(self):
        entered_provider = threading.Event()
        release_provider = threading.Event()

        class BlockingProvider(Provider):
            def generate_chat(self, *, messages, tools, tool_choice):
                self.calls.append((messages, tools, tool_choice))
                entered_provider.set()
                if not release_provider.wait(timeout=5):
                    raise TimeoutError("test did not release provider")
                return {"text": "reply during shutdown", "tool_calls": {"tool_calls": []}}

        self.provider = BlockingProvider()
        session = self.build()
        send_errors = []
        close_errors = []

        def send():
            try:
                session.conversation.send("in flight", request_id="desktop-close-race")
            except Exception as exc:
                send_errors.append(exc)

        worker = threading.Thread(target=send)
        worker.start()
        self.assertTrue(entered_provider.wait(timeout=2), "provider was not entered")

        def close():
            try:
                session.close()
            except Exception as exc:
                close_errors.append(exc)

        closer = threading.Thread(target=close)
        closer.start()
        try:
            # Shutdown must invalidate the facade but must not close SQLite
            # while the provider call still owns an active conversation turn.
            for _ in range(100):
                if session.conversation._invalidated:
                    break
                threading.Event().wait(0.01)
            self.assertTrue(session.conversation._invalidated)
            self.assertFalse(session.runtime.closed)
            self.assertTrue(closer.is_alive())
        finally:
            release_provider.set()

        worker.join(timeout=5)
        closer.join(timeout=5)
        self.assertFalse(worker.is_alive())
        self.assertFalse(closer.is_alive())
        self.assertTrue(session.runtime.closed)
        self.assertEqual(close_errors, [])
        self.assertEqual(len(send_errors), 1)
        self.assertIsInstance(send_errors[0], PermissionError)
        self.assertEqual(self.provider.close_count, 0)

    def test_learning_session_is_injected_into_chat_and_closed_with_runtime(self):
        class Learning:
            def __init__(self):
                self.closed = False
                self.calls = []

            def build_few_shot_patch(self, query, *, max_examples, max_chars):
                self.calls.append((query, max_examples, max_chars))
                return "Approved historical examples."

            def close(self):
                self.closed = True

        learning = Learning()
        session = self.build(learning=learning)
        try:
            session.conversation.send("How do I test this?", request_id="desktop-learning")
            self.assertEqual(
                learning.calls,
                [("How do I test this?", 3, 4_000)],
            )
            self.assertIn(
                "Approved historical examples.",
                self.provider.calls[0][0][0]["content"],
            )
        finally:
            session.close()
        self.assertTrue(learning.closed)

    def test_memory_context_is_bound_to_verified_desktop_session(self):
        class Memory:
            context_enabled = True
            def context_entries(self, *, query, limit):
                return [{"category": "preference", "text": "Use concise answers"}]

            def disable_context(self):
                self.context_enabled = False

        memory = Memory()
        session = self.build(memory=memory)
        try:
            session.conversation.send("Hello", request_id="desktop-memory")
            self.assertIn("Use concise answers", self.provider.calls[0][0][0]["content"])
        finally:
            session.close()
        self.assertFalse(memory.context_enabled)


if __name__ == "__main__":
    unittest.main()
