"""Tests for retry-safe desktop owner-session logout ordering."""
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from ui.desktop.controller import DesktopCompanionController


class DesktopControllerLogoutTests(unittest.TestCase):
    def _controller(self):
        controller = object.__new__(DesktopCompanionController)
        controller.application = Mock()
        controller.identity = SimpleNamespace(
            actor_id="owner-123", session_id="live-session-secret",
        )
        controller.sessions = Mock()
        controller.sessions.logout.return_value = True
        controller._task_session_fenced = False
        controller._auth_session_revoked = False
        controller.application.memory.disable = Mock()
        controller.application.reset_conversation = Mock()
        return controller

    def test_task_fence_failure_keeps_auth_session_live_for_retry(self):
        controller = self._controller()
        controller.application.revoke_session_tasks.side_effect = OSError("sqlite unavailable")

        with self.assertRaisesRegex(RuntimeError, "could not fence tasks"):
            controller.logout()

        controller.application.revoke_session_tasks.assert_called_once_with(
            actor_id="owner-123", session_id="live-session-secret",
        )
        controller.sessions.logout.assert_not_called()
        controller.application.reset_conversation.assert_not_called()
        controller.application.memory.disable.assert_not_called()

    def test_auth_session_is_revoked_only_after_task_fence_succeeds(self):
        controller = self._controller()
        events = []
        controller.application.revoke_session_tasks.side_effect = lambda **_: events.append("task-fence")
        controller.sessions.logout.side_effect = lambda: events.append("auth-revoke") or True

        self.assertTrue(controller.logout())

        self.assertEqual(events, ["task-fence", "auth-revoke"])
        controller.application.reset_conversation.assert_called_once_with()
        controller.application.memory.disable.assert_called_once_with()


    def test_cleanup_retry_does_not_repeat_completed_security_phases(self):
        controller = self._controller()
        controller.application.reset_conversation.side_effect = [
            OSError("temporary conversation cleanup failure"), None,
        ]

        with self.assertRaisesRegex(RuntimeError, "could not complete every logout"):
            controller.logout()

        self.assertTrue(controller._task_session_fenced)
        self.assertTrue(controller._auth_session_revoked)
        self.assertEqual(controller.application.revoke_session_tasks.call_count, 1)
        self.assertEqual(controller.sessions.logout.call_count, 1)

        self.assertFalse(controller.logout())
        self.assertEqual(controller.application.revoke_session_tasks.call_count, 1)
        self.assertEqual(controller.sessions.logout.call_count, 1)
        controller.application.reset_conversation.assert_called_with()


if __name__ == "__main__":
    unittest.main()
