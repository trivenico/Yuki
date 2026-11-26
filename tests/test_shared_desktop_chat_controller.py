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
        cancel_state = {"requested": False}
        cancel = lambda: cancel_state["requested"]
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
        wrapped_cancel = session.conversation.calls[0][1]["cancellation_requested"]
        self.assertTrue(callable(wrapped_cancel))
        self.assertFalse(wrapped_cancel())
        cancel_state["requested"] = True
        self.assertTrue(wrapped_cancel())
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

    def test_logout_waits_until_active_worker_finishes_before_closing_store(self):
        entered = threading.Event()
        release = threading.Event()
        class SlowCancellationConversation(Conversation):
            def send(self, message, **kwargs):
                entered.set()
                self.assert_cancelled = kwargs["cancellation_requested"]
                release.wait(timeout=2)
                return {"status": "cancelled", "answer": ""}

        session = Session()
        session.conversation = SlowCancellationConversation()
        controller = SharedDesktopChatController(session)
        worker_errors = []
        worker = threading.Thread(
            target=lambda: self._capture_error(
                worker_errors, lambda: controller.respond("Slow active turn")
            )
        )
        worker.start()
        self.assertTrue(entered.wait(timeout=1))
        logout_done = threading.Event()
        logout = threading.Thread(target=lambda: (controller.logout(), logout_done.set()))
        logout.start()
        self.assertFalse(logout_done.wait(timeout=0.05))
        self.assertEqual(session.close_count, 0)
        release.set()
        worker.join(timeout=1)
        logout.join(timeout=1)
        self.assertFalse(worker.is_alive())
        self.assertFalse(logout.is_alive())
        self.assertTrue(logout_done.is_set())
        self.assertEqual(session.close_count, 1)
        self.assertTrue(worker_errors)

    def test_logout_waits_for_in_flight_history_read_before_closing_store(self):
        entered = threading.Event()
        release = threading.Event()

        class BlockingReadConversation(Conversation):
            def list_conversations(self, *, limit=50):
                entered.set()
                release.wait(timeout=2)
                return []

        session = Session()
        session.conversation = BlockingReadConversation()
        controller = SharedDesktopChatController(session)
        read_errors = []
        reader = threading.Thread(
            target=lambda: self._capture_error(
                read_errors, controller.list_conversations
            )
        )
        reader.start()
        self.assertTrue(entered.wait(timeout=1))

        logout_done = threading.Event()
        logout = threading.Thread(
            target=lambda: (controller.logout(), logout_done.set())
        )
        logout.start()
        self.assertFalse(logout_done.wait(timeout=0.05))
        self.assertEqual(session.close_count, 0)

        release.set()
        reader.join(timeout=1)
        logout.join(timeout=1)
        self.assertFalse(reader.is_alive())
        self.assertFalse(logout.is_alive())
        self.assertTrue(logout_done.is_set())
        self.assertEqual(read_errors, [])
        self.assertEqual(session.close_count, 1)

    def test_concurrent_logout_calls_close_session_exactly_once(self):
        entered = threading.Event()
        release = threading.Event()

        class BlockingCloseSession(Session):
            def close(self):
                entered.set()
                release.wait(timeout=2)
                super().close()

        session = BlockingCloseSession()
        controller = SharedDesktopChatController(session)
        errors = []
        first = threading.Thread(
            target=lambda: self._capture_error(errors, controller.logout)
        )
        second = threading.Thread(
            target=lambda: self._capture_error(errors, controller.logout)
        )
        first.start()
        self.assertTrue(entered.wait(timeout=1))
        second.start()
        release.set()
        first.join(timeout=1)
        second.join(timeout=1)
        self.assertFalse(first.is_alive())
        self.assertFalse(second.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(session.close_count, 1)


    def test_logout_timeout_keeps_session_open_and_can_be_retried(self):
        entered = threading.Event()
        release = threading.Event()
        class BlockingReadConversation(Conversation):
            def list_conversations(self, *, limit=50):
                entered.set()
                release.wait(timeout=2)
                return []

        session = Session()
        session.conversation = BlockingReadConversation()
        controller = SharedDesktopChatController(
            session, shutdown_timeout_seconds=0.01,
        )
        reader = threading.Thread(target=controller.list_conversations)
        reader.start()
        self.assertTrue(entered.wait(timeout=1))
        with self.assertRaises(TimeoutError):
            controller.logout()
        self.assertEqual(session.close_count, 0)
        with self.assertRaises(RuntimeError):
            controller.list_conversations()
        release.set()
        reader.join(timeout=1)
        self.assertFalse(reader.is_alive())
        controller.logout()
        self.assertEqual(session.close_count, 1)

    def test_shutdown_timeout_configuration_is_bounded(self):
        for value in (True, float("inf"), float("nan"), -1, 3601, 10**10000):
            with self.subTest(value_type=type(value).__name__):
                with self.assertRaises((TypeError, ValueError)):
                    SharedDesktopChatController(Session(), shutdown_timeout_seconds=value)

    def test_failed_session_close_can_be_retried(self):
        class RetryCloseSession(Session):
            def __init__(self):
                super().__init__()
                self.fail_once = True
            def close(self):
                if self.fail_once:
                    self.fail_once = False
                    raise OSError("temporary close failure")
                super().close()

        session = RetryCloseSession()
        controller = SharedDesktopChatController(session)
        with self.assertRaises(OSError):
            controller.logout()
        controller.logout()
        self.assertEqual(session.close_count, 1)

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
