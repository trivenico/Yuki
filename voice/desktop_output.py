"""Desktop speech-output composition with lazy, opt-in local TTS.

No audio device or synthesizer engine is initialized here. Runtime prerequisites
are checked before the UI reports spoken replies as enabled.
"""
from __future__ import annotations

import importlib.util
import shutil
from dataclasses import replace
from pathlib import Path
from typing import Any


def _require_optional_module(name: str, label: str) -> None:
    if importlib.util.find_spec(name) is None:
        raise ValueError(f"The optional {label} package is not installed.")


def create_desktop_voice_output(config: dict[str, Any]):
    """Build a disabled, profile-aware speech session after validating prerequisites."""
    from voice.output_session import VoiceOutputSession
    from voice.profile import DEFAULT_VOICE_PROFILE
    from voice.profiled_tts import ProfiledLocalSpeechOutput

    backend = config.get("tts_backend", "piper")
    if backend not in {"piper", "pyttsx3", "kokoro"}:
        raise ValueError("Configured TTS backend is not supported.")
    rate = config.get("tts_rate", DEFAULT_VOICE_PROFILE.speaking_rate)
    profile = replace(DEFAULT_VOICE_PROFILE, speaking_rate=rate)

    if backend == "kokoro":
        model_dir = config.get("kokoro_model_dir") or None
        lexicon_path = config.get("kokoro_lexicon_path") or None
        model_root = Path(model_dir).expanduser() if model_dir else None
        if model_root is None or not model_root.is_dir():
            raise ValueError("Configure an existing local Kokoro model directory before enabling spoken replies.")
        if any(not (model_root / asset).is_file() for asset in ("model.onnx", "voices.bin", "tokens.txt")):
            raise ValueError("The local Kokoro model directory is incomplete; model.onnx, voices.bin, and tokens.txt are required.")
        if lexicon_path is None or not Path(lexicon_path).expanduser().is_file():
            raise ValueError("Configure an existing explicit Kokoro English lexicon before enabling spoken replies.")
        _require_optional_module("sherpa_onnx", "sherpa-onnx")
        _require_optional_module("numpy", "NumPy")
        _require_optional_module("sounddevice", "sounddevice")
        from voice.kokoro_tts import KokoroTTS

        synthesizer = KokoroTTS(
            model_dir=model_dir,
            lexicon_path=lexicon_path,
            voice=config.get("kokoro_voice", "af_bella"),
            speed=config.get("kokoro_speed", 1.0),
            enabled=False,
        )
    else:
        from voice.tts import LocalTTS

        model_path = config.get("piper_model_path") or None
        if backend == "piper":
            if model_path is None or not Path(model_path).expanduser().is_file():
                raise ValueError("Configure an existing local Piper voice model before enabling spoken replies.")
            if shutil.which("piper") is None:
                raise ValueError("Piper executable is unavailable; install Piper before enabling spoken replies.")
            _require_optional_module("sounddevice", "sounddevice")
            _require_optional_module("soundfile", "soundfile")
        elif backend == "pyttsx3":
            _require_optional_module("pyttsx3", "pyttsx3")

        synthesizer = LocalTTS(
            enabled=False,
            rate=rate,
            backend=backend,
            model_path=model_path,
        )

    profiled_synthesizer = ProfiledLocalSpeechOutput(synthesizer, profile)
    if backend == "kokoro":
        # The dedicated Kokoro speed setting is the explicit backend control;
        # do not silently replace it with the generic WPM profile conversion.
        profiled_synthesizer.synthesizer.speed = float(config.get("kokoro_speed", 1.0))
    return VoiceOutputSession(profiled_synthesizer)
