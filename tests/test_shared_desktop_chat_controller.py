"""Tests for the Qt-independent shared desktop chat controller."""
import threading
import unittest
from ui.desktop.shared_chat_controller import SharedDesktopChatController


class Conversation:
    def __init__(self):
        self.calls = []
        self.enabled = False
    def send(self, message, **kwargs):
        self.calls.append((message, kwargs))
        return {"status": "completed", "answer": "Yuki reply"}
    def set_memory_context_enabled(self, enabled):
        if type(enabled) is not bool:
            raise ValueError("enabled must be bool")
        self.enabled = enabled
    def memory_context_enabled(self):
        return self.enabled


class Session:
    def __init__(self):
        self.conversation = Conversation()
        self.close_count = 0
    def close(self):
        self.close_count += 1


class SharedDesktopChatControllerTests(unittest.TestCase):
    def test_respond_delegates_to_authenticated_shared_conversation(self):
        session = Session()
        controller = SharedDesktopChatController(session)
        deltas = []
        cancel = lambda: False
        delta_callback = deltas.append
        answer = controller.respond(
            "Hello", request_id="desktop-chat-1",
            cancellation_requested=cancel,
            on_text_delta=delta_callback,
        )
        self.assertEqual(answer, "Yuki reply")
        self.assertEqual(session.conversation.calls[0][0], "Hello")
        self.assertEqual(
            session.conversation.calls[0][1]["request_id"], "desktop-chat-1",
        )
        self.assertIs(session.conversation.calls[0][1]["cancellation_requested"], cancel)
        self.assertIs(session.conversation.calls[0][1]["on_text_delta"], delta_callback)

    def test_memory_toggle_delegates_to_authenticated_consent_boundary(self):
        session = Session()
        controller = SharedDesktopChatController(session)
        self.assertFalse(controller.memory_context_enabled())
        controller.set_memory_context(True)
        self.assertTrue(controller.memory_context_enabled())

    def test_logout_closes_session_once_and_rejects_future_work(self):
        session = Session()
        controller = SharedDesktopChatController(session)
        controller.logout()
        controller.close()
        self.assertEqual(session.close_count, 1)
        with self.assertRaises(RuntimeError):
            controller.respond("No longer authenticated")

    def test_logout_signals_cancellation_to_active_chat_worker(self):
        entered = threading.Event()
        cancelled = threading.Event()
        class BlockingConversation(Conversation):
            def send(self, message, **kwargs):
                entered.set()
                check_cancelled = kwargs["cancellation_requested"]
                for _ in range(100):
                    if check_cancelled():
                        cancelled.set()
                        return {"status": "cancelled", "answer": ""}
                    threading.Event().wait(0.005)
                return {"status": "completed", "answer": "unexpected"}

        session = Session()
        session.conversation = BlockingConversation()
        controller = SharedDesktopChatController(session)
        errors = []
        worker = threading.Thread(
            target=lambda: self._capture_error(
                errors, lambda: controller.respond("Active turn")
            )
        )
        worker.start()
        self.assertTrue(entered.wait(timeout=1))
        controller.logout()
        worker.join(timeout=2)
        self.assertFalse(worker.is_alive())
        self.assertTrue(cancelled.is_set())
        self.assertEqual(session.close_count, 1)
        self.assertTrue(errors)

    @staticmethod
    def _capture_error(errors, operation):
        try:
            operation()
        except Exception as exc:
            errors.append(exc)

    def test_non_completed_turn_is_not_presented_as_success(self):
        class FailedConversation(Conversation):
            def send(self, message, **kwargs):
                return {"status": "cancelled", "answer": ""}
        session = Session()
        session.conversation = FailedConversation()
        controller = SharedDesktopChatController(session)
        with self.assertRaisesRegex(RuntimeError, "cancelled"):
            controller.respond("Stop")


if __name__ == "__main__":
    unittest.main()
