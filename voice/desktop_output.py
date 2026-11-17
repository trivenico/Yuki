"""Desktop speech-output composition with lazy, opt-in local TTS.

No audio device or synthesizer engine is initialized here. LocalTTS opens its
backend only when a background worker calls speak_stream().
"""
from __future__ import annotations

from typing import Any


def create_desktop_voice_output(config: dict[str, Any]):
    """Build a disabled speech-output session from validated desktop config."""
    from voice.output_session import VoiceOutputSession
    from voice.tts import LocalTTS

    backend = config.get("tts_backend", "piper")
    if backend not in {"piper", "pyttsx3"}:
        raise ValueError("Configured TTS backend is not supported.")
    rate = config.get("tts_rate", 170)
    model_path = config.get("piper_model_path") or None
    synthesizer = LocalTTS(
        enabled=False,
        rate=rate,
        backend=backend,
        model_path=model_path,
    )
    return VoiceOutputSession(synthesizer)
