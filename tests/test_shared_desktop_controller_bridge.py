"""Tests for the legacy-specialist/shared-chat desktop bridge."""
import threading
import unittest
from ui.desktop.shared_desktop_controller import SharedDesktopControllerBridge


class Chat:
    def __init__(self):
        self.calls = []
        self.close_count = 0
        self.fail_authorization = False
    def respond(self, message, **kwargs):
        self.calls.append((message, kwargs))
        if self.fail_authorization:
            raise PermissionError("session expired")
        return "shared chat reply"
    def set_memory_context(self, enabled):
        self.calls.append(("memory", enabled))
    def memory_context_enabled(self):
        return True
    def logout(self):
        self.close_count += 1


class Specialists:
    def __init__(self, *, fail_logout=False):
        self.calls = []
        self.fail_logout = fail_logout
    def _call(self, name, *args, **kwargs):
        self.calls.append((name, args, kwargs))
        if getattr(self, "fail_authorization", False):
            raise PermissionError("session expired")
        return name
    def logout(self):
        self.calls.append(("logout", (), {}))
        if self.fail_logout:
            raise OSError("revoke failed")
        return True


def add_methods(target):
    names = SharedDesktopControllerBridge._SPECIALIST_METHODS
    for name in names:
        setattr(target, name, lambda *args, _name=name, **kwargs: target._call(_name, *args, **kwargs))


class SharedDesktopControllerBridgeTests(unittest.TestCase):
    def make(self, *, fail_logout=False):
        chat = Chat()
        specialists = Specialists(fail_logout=fail_logout)
        add_methods(specialists)
        return SharedDesktopControllerBridge(chat, specialists), chat, specialists

    def test_chat_and_memory_are_routed_to_shared_controller(self):
        bridge, chat, specialists = self.make()
        self.assertEqual(bridge.respond("hello", request_id="r1"), "shared chat reply")
        bridge.set_memory_context(True)
        self.assertTrue(bridge.memory_context_enabled())
        self.assertEqual(chat.calls[0][0], "hello")
        self.assertEqual(chat.calls[0][1]["request_id"], "r1")
        self.assertEqual(chat.calls[1], ("memory", True))
        self.assertEqual(specialists.calls, [])

    def test_specialist_approval_and_task_methods_remain_delegated(self):
        bridge, chat, specialists = self.make()
        self.assertEqual(bridge.list_specialists(), "list_specialists")
        self.assertEqual(bridge.propose_agent_plan("research"), "propose_agent_plan")
        self.assertEqual(bridge.dispatch_agent_plan("plan-1"), "dispatch_agent_plan")
        self.assertEqual(
            bridge.approve_task("task-1", expected_version=3), "approve_task",
        )
        self.assertEqual(
            [call[0] for call in specialists.calls],
            ["list_specialists", "propose_agent_plan", "dispatch_agent_plan", "approve_task"],
        )
        self.assertEqual(chat.calls, [])

    def test_logout_revokes_legacy_session_and_always_closes_shared_chat(self):
        bridge, chat, specialists = self.make()
        self.assertTrue(bridge.logout())
        self.assertEqual(specialists.calls[-1][0], "logout")
        self.assertEqual(chat.close_count, 1)
        with self.assertRaises(RuntimeError):
            bridge.respond("after logout")

    def test_shared_chat_is_closed_even_if_session_revocation_fails(self):
        bridge, chat, specialists = self.make(fail_logout=True)
        with self.assertRaisesRegex(RuntimeError, "logout"):
            bridge.logout()
        self.assertEqual(chat.close_count, 1)

    def test_failed_logout_can_retry_specialist_session_revocation(self):
        bridge, chat, specialists = self.make(fail_logout=True)
        with self.assertRaisesRegex(RuntimeError, "logout"):
            bridge.logout()
        self.assertEqual(chat.close_count, 1)
        specialists.fail_logout = False
        self.assertTrue(bridge.logout())
        self.assertEqual(chat.close_count, 2)
        self.assertEqual(
            [call[0] for call in specialists.calls].count("logout"), 2,
        )

    def test_chat_authorization_failure_closes_both_session_surfaces(self):
        bridge, chat, specialists = self.make()
        chat.fail_authorization = True
        with self.assertRaisesRegex(PermissionError, "expired"):
            bridge.respond("hello")
        self.assertEqual(chat.close_count, 1)
        self.assertEqual(specialists.calls[-1][0], "logout")

    def test_specialist_authorization_failure_closes_both_session_surfaces(self):
        bridge, chat, specialists = self.make()
        specialists.fail_authorization = True
        with self.assertRaisesRegex(PermissionError, "expired"):
            bridge.list_specialists()
        self.assertEqual(chat.close_count, 1)
        self.assertEqual(specialists.calls[-1][0], "logout")


    def test_concurrent_logout_closes_authorization_gate_and_runs_once(self):
        entered = threading.Event()
        release = threading.Event()

        class BlockingSpecialists(Specialists):
            def logout(self):
                self.calls.append(("logout", (), {}))
                entered.set()
                if not release.wait(timeout=2):
                    raise TimeoutError("test did not release logout")
                return True

        chat = Chat()
        specialists = BlockingSpecialists()
        add_methods(specialists)
        bridge = SharedDesktopControllerBridge(chat, specialists)
        results = []
        worker = threading.Thread(target=lambda: results.append(bridge.logout()))
        worker.start()
        self.assertTrue(entered.wait(timeout=1))
        self.assertFalse(bridge.logout())
        with self.assertRaises(RuntimeError):
            bridge.respond("must be rejected during logout")
        release.set()
        worker.join(timeout=2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(results, [True])
        self.assertEqual(chat.close_count, 1)
        self.assertEqual(
            [call[0] for call in specialists.calls].count("logout"), 1,
        )

    def test_bridge_rejects_missing_specialist_contract(self):
        with self.assertRaises(TypeError):
            SharedDesktopControllerBridge(Chat(), object())


if __name__ == "__main__":
    unittest.main()
