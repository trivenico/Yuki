import unittest

from voice.session import VoiceSessionController, VoiceSessionError, VoiceState


class VoiceSessionControllerTests(unittest.TestCase):
    def setUp(self):
        self.voice = VoiceSessionController()

    def test_hands_free_requires_os_permission(self):
        with self.assertRaises(VoiceSessionError):
            self.voice.enable_hands_free(permission_granted=False)
        self.assertEqual(self.voice.state, VoiceState.OFF)

    def test_hands_free_starts_in_standby_not_capture(self):
        self.assertEqual(
            self.voice.enable_hands_free(permission_granted=True),
            VoiceState.STANDBY,
        )
        self.assertFalse(self.voice.microphone_may_be_open)

    def test_wake_phrase_opens_only_activated_capture_state(self):
        self.voice.enable_hands_free(permission_granted=True)
        self.assertEqual(self.voice.wake_detected(), VoiceState.CAPTURING)
        self.assertEqual(self.voice.capture_source, "wake_word")
        self.assertTrue(self.voice.microphone_may_be_open)

    def test_wake_is_rejected_when_hands_free_is_off(self):
        with self.assertRaises(VoiceSessionError):
            self.voice.wake_detected()

    def test_wake_cannot_start_twice(self):
        self.voice.enable_hands_free(permission_granted=True)
        self.voice.wake_detected()
        with self.assertRaises(VoiceSessionError):
            self.voice.wake_detected()

    def test_hands_free_conversation_and_follow_up_cycle(self):
        self.voice.enable_hands_free(permission_granted=True)
        self.voice.wake_detected()
        self.assertEqual(self.voice.capture_finished(), VoiceState.PROCESSING)
        self.assertFalse(self.voice.microphone_may_be_open)
        self.assertEqual(self.voice.transcript_ready(has_text=True), VoiceState.THINKING)
        self.assertEqual(self.voice.response_ready(), VoiceState.SPEAKING)
        self.assertEqual(self.voice.speech_finished(), VoiceState.FOLLOW_UP)
        self.assertEqual(self.voice.follow_up_detected(), VoiceState.CAPTURING)
        self.assertEqual(self.voice.capture_finished(), VoiceState.PROCESSING)
        self.assertEqual(self.voice.transcript_ready(has_text=False), VoiceState.STANDBY)

    def test_follow_up_timeout_returns_to_local_standby(self):
        self.voice.enable_hands_free(permission_granted=True)
        self.voice.wake_detected()
        self.voice.capture_finished()
        self.voice.transcript_ready(has_text=True)
        self.voice.response_ready()
        self.voice.speech_finished()
        self.assertEqual(self.voice.follow_up_timeout(), VoiceState.STANDBY)

    def test_empty_response_returns_to_follow_up_without_playback(self):
        self.voice.enable_hands_free(permission_granted=True)
        self.voice.wake_detected()
        self.voice.capture_finished()
        self.voice.transcript_ready(has_text=True)
        self.assertEqual(self.voice.response_ready(has_speech=False), VoiceState.FOLLOW_UP)

    def test_push_to_talk_is_available_without_hands_free(self):
        self.assertEqual(
            self.voice.start_push_to_talk(permission_granted=True),
            VoiceState.CAPTURING,
        )
        self.assertEqual(self.voice.capture_source, "push_to_talk")
        self.voice.capture_finished()
        self.voice.transcript_ready(has_text=True)
        self.voice.response_ready()
        self.assertEqual(self.voice.speech_finished(), VoiceState.OFF)

    def test_mute_stops_capture_and_requires_explicit_unmute(self):
        self.voice.enable_hands_free(permission_granted=True)
        self.voice.wake_detected()
        self.assertEqual(self.voice.mute(), VoiceState.MUTED)
        self.assertFalse(self.voice.microphone_may_be_open)
        self.assertIsNone(self.voice.capture_source)
        with self.assertRaises(VoiceSessionError):
            self.voice.wake_detected()
        self.assertEqual(self.voice.unmute(), VoiceState.STANDBY)

    def test_mute_during_follow_up_stops_future_capture(self):
        self.voice.enable_hands_free(permission_granted=True)
        self.voice.wake_detected()
        self.voice.capture_finished()
        self.voice.transcript_ready(has_text=True)
        self.voice.response_ready()
        self.voice.speech_finished()
        self.voice.mute()
        with self.assertRaises(VoiceSessionError):
            self.voice.follow_up_detected()

    def test_permission_revocation_disables_voice(self):
        self.voice.enable_hands_free(permission_granted=True)
        self.voice.wake_detected()
        self.assertEqual(self.voice.permission_revoked(), VoiceState.OFF)
        self.assertFalse(self.voice.hands_free_enabled)
        self.assertFalse(self.voice.microphone_may_be_open)

    def test_logout_clears_voice_state(self):
        self.voice.enable_hands_free(permission_granted=True)
        self.voice.wake_detected()
        self.assertEqual(self.voice.logout(), VoiceState.OFF)
        self.assertIsNone(self.voice.capture_source)

    def test_adapter_failure_fails_closed(self):
        self.voice.enable_hands_free(permission_granted=True)
        self.voice.wake_detected()
        self.assertEqual(self.voice.fail_closed(), VoiceState.MUTED)
        self.assertFalse(self.voice.hands_free_enabled)
        self.assertFalse(self.voice.microphone_may_be_open)
        with self.assertRaises(VoiceSessionError):
            self.voice.unmute()
        self.assertEqual(self.voice.unmute(), VoiceState.OFF)

    def test_invalid_boolean_flags_are_rejected(self):
        with self.assertRaises(TypeError):
            self.voice.enable_hands_free(permission_granted=1)
        with self.assertRaises(TypeError):
            self.voice.start_push_to_talk(permission_granted="yes")
        with self.assertRaises(TypeError):
            self.voice.transcript_ready(has_text=1)

    def test_out_of_order_events_are_rejected(self):
        with self.assertRaises(VoiceSessionError):
            self.voice.capture_finished()
        with self.assertRaises(VoiceSessionError):
            self.voice.speech_finished()
        with self.assertRaises(VoiceSessionError):
            self.voice.follow_up_timeout()

    def test_disable_is_idempotent_and_clears_mute(self):
        self.voice.mute()
        self.assertEqual(self.voice.disable(), VoiceState.OFF)
        self.assertEqual(self.voice.disable(), VoiceState.OFF)
        self.assertEqual(
            self.voice.enable_hands_free(permission_granted=True),
            VoiceState.STANDBY,
        )


if __name__ == "__main__":
    unittest.main()
