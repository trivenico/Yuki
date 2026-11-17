"""Factory for an explicitly started local openWakeWord session."""
from __future__ import annotations

from voice.openwakeword_detector import OpenWakeWordDetector
from voice.wakeword_audio import SoundDeviceWakeWordAudioSource
from voice.wakeword_session import WakeWordSession


def create_openwakeword_session(
    *,
    model_path: str,
    on_wake,
    threshold: float = 0.5,
    trigger_frames: int = 2,
    on_state=None,
    on_error=None,
    model_factory=None,
    sounddevice_module=None,
) -> WakeWordSession:
    """Create a lazy local wake session around one user-supplied ONNX model.

    The detector and audio device are created only when WakeWordSession.start()
    is explicitly called. The microphone block size is matched to the model's
    80 ms / 1280-sample input contract.
    """
    detector_ref = {}

    def detector_factory():
        detector = OpenWakeWordDetector(
            model_path=model_path,
            threshold=threshold,
            trigger_frames=trigger_frames,
            model_factory=model_factory,
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
