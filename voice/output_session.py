"""Explicitly enabled, bounded speech-output session.

This policy layer never creates a synthesizer, opens an audio device, or speaks
at construction time. A UI worker must call speak_reply() only after the user
has explicitly enabled voice output for the current authenticated session.
"""
from __future__ import annotations

from threading import RLock


class VoiceOutputError(RuntimeError):
    """Speech output could not safely complete."""


class VoiceOutputSession:
    MAX_REPLY_CHARS = 20000
    MAX_CHUNK_CHARS = 2000
    MAX_CHUNKS = 100

    def __init__(self, synthesizer) -> None:
        if not callable(getattr(synthesizer, "speak_stream", None)):
            raise TypeError("synthesizer must provide speak_stream().")
        if not callable(getattr(synthesizer, "stop", None)):
            raise TypeError("synthesizer must provide stop().")
        self._synthesizer = synthesizer
        self._lock = RLock()
        self._enabled = False
        self._closed = False
        self._speaking = False
        self._generation = 0
        # Keep a supplied backend silent even if it was constructed enabled.
        if hasattr(self._synthesizer, "enabled"):
            self._synthesizer.enabled = False

    @property
    def enabled(self) -> bool:
        with self._lock:
            return self._enabled and not self._closed

    @property
    def speaking(self) -> bool:
        with self._lock:
            return self._speaking

    def enable(self) -> None:
        """Enable output only after a UI has obtained explicit user consent."""
        with self._lock:
            if self._closed:
                raise VoiceOutputError("This voice-output session is closed.")
            self._enabled = True
            if hasattr(self._synthesizer, "enabled"):
                self._synthesizer.enabled = True

    def disable(self) -> None:
        """Revoke consent and interrupt active playback."""
        with self._lock:
            self._enabled = False
            self._generation += 1
            if hasattr(self._synthesizer, "enabled"):
                self._synthesizer.enabled = False
        self.stop()

    def stop(self) -> None:
        """Request immediate backend interruption; safe to call from a Stop button."""
        try:
            self._synthesizer.stop()
        except Exception as exc:
            raise VoiceOutputError("Speech playback could not be stopped safely.") from exc

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._enabled = False
            self._generation += 1
            if hasattr(self._synthesizer, "enabled"):
                self._synthesizer.enabled = False
        self.stop()

    @staticmethod
    def _chunks(text: str) -> list[str]:
        return [
            text[index:index + VoiceOutputSession.MAX_CHUNK_CHARS]
            for index in range(0, len(text), VoiceOutputSession.MAX_CHUNK_CHARS)
        ]

    def speak_reply(self, text: str, *, voice_mode_active: bool = False):
        """Speak one reply only inside an explicitly active voice-output mode.

        Call this method from a background worker, never from the GUI thread.
        """
        if not isinstance(text, str) or not text.strip():
            raise ValueError("Reply text must be non-empty text.")
        text = text.strip()
        if len(text) > self.MAX_REPLY_CHARS:
            raise ValueError("Reply exceeds the 20000-character speech limit.")
        chunks = self._chunks(text)
        if len(chunks) > self.MAX_CHUNKS:
            raise ValueError("Reply exceeds the speech chunk limit.")
        with self._lock:
            if self._closed or not self._enabled or voice_mode_active is not True:
                raise VoiceOutputError("Voice output is not explicitly enabled.")
            if self._speaking:
                raise VoiceOutputError("Speech output is already in progress.")
            self._speaking = True
            generation = self._generation
        try:
            with self._lock:
                if self._closed or not self._enabled or generation != self._generation:
                    raise VoiceOutputError("Speech output permission was revoked.")
            return self._synthesizer.speak_stream(chunks)
        finally:
            with self._lock:
                self._speaking = False
