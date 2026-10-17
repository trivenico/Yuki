"""Voice readiness/consent facade; does not start background recording."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class VoiceReadiness:
    enabled: bool
    stt_model_present: bool
    tts_model_present: bool
    wakeword_model_present: bool
    background_listening: bool
    reasons: tuple[str, ...]


class VoiceProduction:
    """Reports prerequisites and requires explicit consent for each capture."""

    def __init__(self, *, enabled: bool = False, stt_model_path: str | None = None,
                 tts_model_path: str | None = None, wakeword_model_path: str | None = None):
        self.enabled = bool(enabled)
        self.stt_model_path = Path(stt_model_path).expanduser() if stt_model_path else None
        self.tts_model_path = Path(tts_model_path).expanduser() if tts_model_path else None
        self.wakeword_model_path = Path(wakeword_model_path).expanduser() if wakeword_model_path else None

    def readiness(self) -> VoiceReadiness:
        stt = bool(self.stt_model_path and self.stt_model_path.is_file())
        tts = bool(self.tts_model_path and self.tts_model_path.is_file())
        wake = bool(self.wakeword_model_path and self.wakeword_model_path.is_file())
        reasons = []
        if not self.enabled:
            reasons.append("voice_disabled_by_configuration")
        if not stt:
            reasons.append("speech_to_text_model_missing")
        if not tts:
            reasons.append("text_to_speech_model_missing")
        if not wake:
            reasons.append("wakeword_model_missing")
        # This module intentionally never launches a background microphone listener.
        return VoiceReadiness(self.enabled, stt, tts, wake, False, tuple(reasons))

    @staticmethod
    def authorize_capture(*, explicit_consent: bool, max_seconds: int = 30) -> int:
        if explicit_consent is not True:
            raise PermissionError("Explicit user consent is required before recording audio.")
        if isinstance(max_seconds, bool) or not isinstance(max_seconds, int) or not 1 <= max_seconds <= 120:
            raise ValueError("max_seconds must be an integer between 1 and 120.")
        return max_seconds
