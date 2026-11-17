"""Desktop speech-output composition with lazy, opt-in local TTS.

No audio device or synthesizer engine is initialized here. Runtime prerequisites
are checked before the UI reports spoken replies as enabled.
"""
from __future__ import annotations

import importlib.util
import shutil
from pathlib import Path
from typing import Any


def create_desktop_voice_output(config: dict[str, Any]):
    """Build a disabled, profile-aware speech session after validating prerequisites."""
    from voice.output_session import VoiceOutputSession
    from voice.profile import DEFAULT_VOICE_PROFILE
    from voice.profiled_tts import ProfiledLocalSpeechOutput
    from voice.tts import LocalTTS

    backend = config.get("tts_backend", "piper")
    if backend not in {"piper", "pyttsx3"}:
        raise ValueError("Configured TTS backend is not supported.")
    rate = config.get("tts_rate", DEFAULT_VOICE_PROFILE.speaking_rate)
    model_path = config.get("piper_model_path") or None
    if backend == "piper":
        if model_path is None or not Path(model_path).expanduser().is_file():
            raise ValueError("Configure an existing local Piper voice model before enabling spoken replies.")
        if shutil.which("piper") is None:
            raise ValueError("Piper executable is unavailable; install Piper before enabling spoken replies.")
        if importlib.util.find_spec("sounddevice") is None or importlib.util.find_spec("soundfile") is None:
            raise ValueError("Piper playback requires the optional sounddevice and soundfile packages.")
    elif importlib.util.find_spec("pyttsx3") is None:
        raise ValueError("The optional pyttsx3 package is not installed.")
    synthesizer = LocalTTS(
        enabled=False,
        rate=rate,
        backend=backend,
        model_path=model_path,
    )
    profiled_synthesizer = ProfiledLocalSpeechOutput(
        synthesizer, DEFAULT_VOICE_PROFILE,
    )
    return VoiceOutputSession(profiled_synthesizer)
