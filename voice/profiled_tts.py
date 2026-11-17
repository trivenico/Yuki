"""Honest capability reporting when applying a Yuki voice profile to local TTS.

A voice profile expresses the requested presentation. It does not make a
synthesizer support a voice, accent, pitch, or expressive acting style.
"""
from __future__ import annotations

from dataclasses import dataclass
from threading import Lock
from typing import FrozenSet

from voice.profile import VoiceProfile


@dataclass(frozen=True)
class VoiceOutputCapabilities:
    backend: str
    supported_controls: FrozenSet[str]
    unsupported_controls: FrozenSet[str]
    requested_style: str

    def supports(self, control: str) -> bool:
        return control in self.supported_controls


class ProfiledLocalSpeechOutput:
    """Apply only controls the wrapped local synthesizer can actually honor."""

    _PROFILE_CONTROLS = frozenset({"speaking_rate", "pitch", "expressiveness", "style"})
    _MAX_FALLBACK_CHUNKS = 100
    _MAX_FALLBACK_CHARACTERS = 20_000

    def __init__(self, synthesizer, profile: VoiceProfile):
        if not callable(getattr(synthesizer, "speak", None)):
            raise TypeError("synthesizer must provide a callable speak(text) method")
        if not callable(getattr(synthesizer, "stop", None)):
            raise TypeError("synthesizer must provide a callable stop() method")
        if not isinstance(profile, VoiceProfile):
            raise TypeError("profile must be a VoiceProfile")
        self.synthesizer = synthesizer
        self.profile = profile
        self._stream_lock = Lock()
        self._stream_generation = 0
        backend = getattr(synthesizer, "backend", "unknown")
        supported = set()
        # The current pyttsx3 adapter exposes rate. The current Piper adapter
        # does not pass a speed/length parameter to its executable.
        if backend == "pyttsx3" and hasattr(synthesizer, "rate"):
            synthesizer.rate = profile.speaking_rate
            supported.add("speaking_rate")
        elif backend == "kokoro" and hasattr(synthesizer, "speed"):
            # Kokoro uses a relative speed multiplier; normalize around the
            # existing Yuki profile's 170 WPM reference point.
            synthesizer.speed = profile.speaking_rate / 170.0
            supported.add("speaking_rate")
        self.capabilities = VoiceOutputCapabilities(
            backend=str(backend),
            supported_controls=frozenset(supported),
            unsupported_controls=self._PROFILE_CONTROLS - supported,
            requested_style=profile.style,
        )

    @property
    def enabled(self) -> bool:
        """Expose the wrapped backend's enable state when it has one."""
        return bool(getattr(self.synthesizer, "enabled", False))

    @enabled.setter
    def enabled(self, value: bool) -> None:
        if hasattr(self.synthesizer, "enabled"):
            self.synthesizer.enabled = value is True

    def stop(self) -> None:
        """Invalidate pending chunks before asking the backend to stop playback."""
        with self._stream_lock:
            self._stream_generation += 1
        self.synthesizer.stop()

    def speak(self, text: str):
        """Speak only when explicitly called by the voice-session controller."""
        return self.synthesizer.speak(text)

    def _bounded_chunks(self, chunks):
        count = 0
        total_characters = 0
        for chunk in chunks:
            if not isinstance(chunk, str):
                raise ValueError("Speech chunks must be text.")
            if not chunk.strip():
                continue
            if len(chunk) > 2000:
                raise ValueError("Each speech chunk must be at most 2000 characters.")
            if count >= self._MAX_FALLBACK_CHUNKS:
                raise ValueError("Speech stream exceeds the 100-chunk limit.")
            total_characters += len(chunk)
            if total_characters > self._MAX_FALLBACK_CHARACTERS:
                raise ValueError("Speech stream exceeds the 20000-character limit.")
            count += 1
            yield chunk

    def speak_stream(self, chunks):
        """Validate the entire bounded stream before allowing any audio side effect."""
        # Materialization is safe because _bounded_chunks caps the collection at
        # 100 chunks / 20,000 characters. It also prevents a malformed late
        # chunk from causing partial speech before the input is rejected.
        with self._stream_lock:
            generation = self._stream_generation
        validated_chunks = list(self._bounded_chunks(chunks))

        def guarded_chunks():
            for chunk in validated_chunks:
                with self._stream_lock:
                    if generation != self._stream_generation:
                        raise RuntimeError("Speech stream was stopped before all chunks completed.")
                yield chunk

        stream = getattr(self.synthesizer, "speak_stream", None)
        if callable(stream):
            # Keep the generation guard lazy: a stop between validation and
            # backend startup must not be erased by a backend resetting its
            # own interruption flag at the beginning of speak_stream().
            return stream(guarded_chunks())
        count = 0
        for chunk in guarded_chunks():
            self.synthesizer.speak(chunk)
            count += 1
        return {"chunks_spoken": count, "interrupted": False}

    def capability_report(self) -> dict:
        """Return machine-readable requested-vs-supported profile controls."""
        return {
            "profile_id": self.profile.profile_id,
            "requested_style": self.profile.style,
            "backend": self.capabilities.backend,
            "supported_controls": sorted(self.capabilities.supported_controls),
            "unsupported_controls": sorted(self.capabilities.unsupported_controls),
        }
