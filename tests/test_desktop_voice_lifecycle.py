"""Regression tests for desktop voice-session retirement."""

import unittest
from unittest.mock import Mock

from ui.desktop.launch import _retire_speech_session, _retire_voice_session


class DesktopVoiceLifecycleTests(unittest.TestCase):
    def test_revocation_defers_close_while_capture_worker_owns_session(self):
        session = Mock()
        status = Mock()

        failed = _retire_voice_session(
            session, capture_active=True, status_label=status,
        )

        self.assertFalse(failed)
        session.disable.assert_called_once_with()
        session.close.assert_not_called()

    def test_revocation_closes_session_when_no_capture_worker_is_active(self):
        session = Mock()
        status = Mock()

        failed = _retire_voice_session(
            session, capture_active=False, status_label=status,
        )

        self.assertFalse(failed)
        session.disable.assert_called_once_with()
        session.close.assert_called_once_with()

    def test_speech_revocation_defers_close_while_playback_worker_owns_session(self):
        session = Mock()
        status = Mock()

        failed = _retire_speech_session(
            session, playback_active=True, status_label=status,
        )

        self.assertFalse(failed)
        session.disable.assert_called_once_with()
        session.close.assert_not_called()

    def test_speech_revocation_closes_session_when_playback_is_idle(self):
        session = Mock()
        status = Mock()

        failed = _retire_speech_session(
            session, playback_active=False, status_label=status,
        )

        self.assertFalse(failed)
        session.disable.assert_called_once_with()
        session.close.assert_called_once_with()

    def test_disable_failure_does_not_skip_safe_close_when_idle(self):
        session = Mock()
        session.disable.side_effect = RuntimeError("device interruption failed")
        status = Mock()

        failed = _retire_voice_session(
            session, capture_active=False, status_label=status,
        )

        self.assertTrue(failed)
        session.close.assert_called_once_with()
        status.setText.assert_called_once()


if __name__ == "__main__":
    unittest.main()
