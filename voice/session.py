"""Fail-closed voice conversation state machine; this module never opens a microphone.

Audio capture, wake-word inference, transcription, and playback are deliberately
provided by separate adapters. This controller only authorizes state transitions.
"""
from __future__ import annotations

from enum import Enum


class VoiceSessionError(RuntimeError):
    """Raised when a voice-session transition is not currently permitted."""


class VoiceState(str, Enum):
    OFF = "off"
    STANDBY = "standby"
    CAPTURING = "capturing"
    PROCESSING = "processing"
    THINKING = "thinking"
    SPEAKING = "speaking"
    FOLLOW_UP = "follow_up"
    MUTED = "muted"


class VoiceSessionController:
    """Deterministic, side-effect-free state controller for desktop voice sessions.

    Hands-free standby is only entered after explicit enablement and a positive
    OS microphone-permission check. Push-to-talk can start a bounded capture
    without enabling hands-free mode. This class does not itself capture audio.
    """

    def __init__(self) -> None:
        self._state = VoiceState.OFF
        self._hands_free_enabled = False
        self._permission_granted = False
        self._muted = False
        self._capture_source: str | None = None

    @property
    def state(self) -> VoiceState:
        return self._state

    @property
    def hands_free_enabled(self) -> bool:
        return self._hands_free_enabled

    @property
    def capture_source(self) -> str | None:
        return self._capture_source

    @property
    def microphone_may_be_open(self) -> bool:
        """Whether an external capture adapter may currently capture audio."""
        return self._state == VoiceState.CAPTURING

    def enable_hands_free(self, *, permission_granted: bool) -> VoiceState:
        if not isinstance(permission_granted, bool):
            raise TypeError("permission_granted must be a bool")
        if not permission_granted:
            raise VoiceSessionError("OS microphone permission is required.")
        if self._muted:
            raise VoiceSessionError("Unmute voice mode explicitly before enabling it.")
        if self._state not in {VoiceState.OFF, VoiceState.STANDBY}:
            raise VoiceSessionError("Hands-free mode can only be enabled while idle.")
        self._permission_granted = True
        self._hands_free_enabled = True
        self._state = VoiceState.STANDBY
        return self._state

    def start_push_to_talk(self, *, permission_granted: bool) -> VoiceState:
        if not isinstance(permission_granted, bool):
            raise TypeError("permission_granted must be a bool")
        if not permission_granted:
            raise VoiceSessionError("OS microphone permission is required.")
        if self._muted or self._state not in {VoiceState.OFF, VoiceState.STANDBY}:
            raise VoiceSessionError("Push-to-talk is unavailable in the current state.")
        self._permission_granted = True
        self._capture_source = "push_to_talk"
        self._state = VoiceState.CAPTURING
        return self._state

    def wake_detected(self) -> VoiceState:
        if (self._state != VoiceState.STANDBY or not self._hands_free_enabled
                or not self._permission_granted or self._muted):
            raise VoiceSessionError("Wake activation is not permitted in the current state.")
        self._capture_source = "wake_word"
        self._state = VoiceState.CAPTURING
        return self._state

    def capture_finished(self) -> VoiceState:
        self._require(VoiceState.CAPTURING)
        self._state = VoiceState.PROCESSING
        return self._state

    def transcript_ready(self, *, has_text: bool) -> VoiceState:
        self._require(VoiceState.PROCESSING)
        if not isinstance(has_text, bool):
            raise TypeError("has_text must be a bool")
        if not has_text:
            return self._return_to_idle()
        self._state = VoiceState.THINKING
        return self._state

    def response_ready(self, *, has_speech: bool = True) -> VoiceState:
        self._require(VoiceState.THINKING)
        if not isinstance(has_speech, bool):
            raise TypeError("has_speech must be a bool")
        self._state = VoiceState.SPEAKING if has_speech else self._after_turn()
        return self._state

    def speech_finished(self) -> VoiceState:
        self._require(VoiceState.SPEAKING)
        self._state = self._after_turn()
        return self._state

    def follow_up_detected(self) -> VoiceState:
        self._require(VoiceState.FOLLOW_UP)
        if not self._hands_free_enabled or not self._permission_granted or self._muted:
            raise VoiceSessionError("Follow-up capture is not permitted.")
        self._capture_source = "follow_up"
        self._state = VoiceState.CAPTURING
        return self._state

    def follow_up_timeout(self) -> VoiceState:
        self._require(VoiceState.FOLLOW_UP)
        self._capture_source = None
        self._state = VoiceState.STANDBY if self._hands_free_enabled else VoiceState.OFF
        return self._state

    def mute(self) -> VoiceState:
        """Immediately revoke permission for any external capture adapter."""
        self._muted = True
        self._capture_source = None
        self._state = VoiceState.MUTED
        return self._state

    def unmute(self) -> VoiceState:
        if not self._muted:
            raise VoiceSessionError("Voice mode is not muted.")
        self._muted = False
        if self._hands_free_enabled and self._permission_granted:
            self._state = VoiceState.STANDBY
        else:
            self._state = VoiceState.OFF
        return self._state

    def disable(self) -> VoiceState:
        self._hands_free_enabled = False
        self._permission_granted = False
        self._capture_source = None
        self._muted = False
        self._state = VoiceState.OFF
        return self._state

    def permission_revoked(self) -> VoiceState:
        return self.disable()

    def logout(self) -> VoiceState:
        return self.disable()

    def fail_closed(self) -> VoiceState:
        """Stop capture after any adapter/device/runtime failure."""
        self._hands_free_enabled = False
        self._permission_granted = False
        self._capture_source = None
        self._muted = True
        self._state = VoiceState.MUTED
        return self._state

    def _after_turn(self) -> VoiceState:
        self._capture_source = None
        if self._hands_free_enabled and self._permission_granted and not self._muted:
            return VoiceState.FOLLOW_UP
        return VoiceState.OFF

    def _return_to_idle(self) -> VoiceState:
        self._capture_source = None
        self._state = (VoiceState.STANDBY if self._hands_free_enabled
                       and self._permission_granted and not self._muted else VoiceState.OFF)
        return self._state

    def _require(self, expected: VoiceState) -> None:
        if self._state != expected:
            raise VoiceSessionError(
                f"Transition requires {expected.value}; current state is {self._state.value}."
            )
