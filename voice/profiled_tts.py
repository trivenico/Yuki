"""Honest capability reporting when applying a Yuki voice profile to local TTS.

A voice profile expresses the requested presentation. It does not make a
synthesizer support a voice, accent, pitch, or expressive acting style.
"""
from __future__ import annotations

from dataclasses import dataclass
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
        if not isinstance(profile, VoiceProfile):
            raise TypeError("profile must be a VoiceProfile")
        self.synthesizer = synthesizer
        self.profile = profile
        backend = getattr(synthesizer, "backend", "unknown")
        supported = set()
        # The current pyttsx3 adapter exposes rate. The current Piper adapter
        # does not pass a speed/length parameter to its executable.
        if backend == "pyttsx3" and hasattr(synthesizer, "rate"):
            synthesizer.rate = profile.speaking_rate
            supported.add("speaking_rate")
        self.capabilities = VoiceOutputCapabilities(
            backend=str(backend),
            supported_controls=frozenset(supported),
            unsupported_controls=self._PROFILE_CONTROLS - supported,
            requested_style=profile.style,
        )

    def speak(self, text: str):
        """Speak only when explicitly called by the voice-session controller."""
        return self.synthesizer.speak(text)

    def speak_stream(self, chunks):
        """Delegate streaming when available; otherwise speak chunks serially."""
        stream = getattr(self.synthesizer, "speak_stream", None)
        if callable(stream):
            return stream(chunks)
        count = 0
        total_characters = 0
        for chunk in chunks:
            if not isinstance(chunk, str) or not chunk.strip():
                continue
            if len(chunk) > 2000:
                raise ValueError("Each speech chunk must be at most 2000 characters.")
            if count >= self._MAX_FALLBACK_CHUNKS:
                raise ValueError("Speech stream exceeds the 100-chunk limit.")
            total_characters += len(chunk)
            if total_characters > self._MAX_FALLBACK_CHARACTERS:
                raise ValueError("Speech stream exceeds the 20000-character limit.")
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
