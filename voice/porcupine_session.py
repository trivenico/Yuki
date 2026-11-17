"""Factory for an explicitly started Porcupine + local microphone wake session."""
from __future__ import annotations

from voice.porcupine_detector import PorcupineWakeWordDetector
from voice.wakeword_audio import SoundDeviceWakeWordAudioSource
from voice.wakeword_session import WakeWordSession


def create_porcupine_wakeword_session(
    *,
    access_key: str,
    keyword_model_path: str,
    on_wake,
    sensitivity: float = 0.5,
    on_state=None,
    on_error=None,
    porcupine_module=None,
    sounddevice_module=None,
) -> WakeWordSession:
    """Create a lazy local wake session from a user-owned Picovoice model.

    No SDK/model/device is loaded here. WakeWordSession calls detector_factory
    first on explicit start; audio_source_factory then matches the engine's
    sample rate and exact frame length.
    """
    detector_ref = {}

    def detector_factory():
        detector = PorcupineWakeWordDetector(
            access_key=access_key,
            keyword_model_path=keyword_model_path,
            sensitivity=sensitivity,
            porcupine_module=porcupine_module,
        )
        detector_ref["detector"] = detector
        return detector

    def audio_source_factory():
        detector = detector_ref.get("detector")
        if detector is None:
            raise RuntimeError("Wake-word detector must be initialized before audio.")
        return SoundDeviceWakeWordAudioSource(
            sample_rate=detector.sample_rate,
            frame_length=detector.frame_length,
            sounddevice_module=sounddevice_module,
        )

    return WakeWordSession(
        audio_source_factory=audio_source_factory,
        detector_factory=detector_factory,
        on_wake=on_wake,
        on_state=on_state,
        on_error=on_error,
    )
