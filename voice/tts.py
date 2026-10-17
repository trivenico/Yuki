"""Optional offline TTS adapter. Disabled unless explicitly enabled by the caller."""
from __future__ import annotations


class TTSError(RuntimeError):
    pass


class LocalTTS:
    def __init__(self, *, enabled: bool = False, rate: int = 170, volume: float = 1.0):
        self.enabled = enabled
        if isinstance(rate, bool) or not isinstance(rate, int) or not 80 <= rate <= 260:
            raise ValueError("rate must be between 80 and 260.")
        if not isinstance(volume, (int, float)) or isinstance(volume, bool) or not 0.0 <= volume <= 1.0:
            raise ValueError("volume must be between 0 and 1.")
        self.rate = rate
        self.volume = float(volume)

    def speak(self, text: str) -> None:
        if not self.enabled:
            raise TTSError("Voice output is disabled.")
        if not isinstance(text, str) or not text.strip() or len(text) > 2000:
            raise ValueError("Text must be 1-2000 characters.")
        try:
            import pyttsx3
        except ImportError as exc:
            raise TTSError("Install the optional pyttsx3 package to enable local TTS.") from exc
        try:
            engine = pyttsx3.init()
            engine.setProperty("rate", self.rate)
            engine.setProperty("volume", self.volume)
            engine.say(text)
            engine.runAndWait()
            engine.stop()
        except Exception as exc:
            raise TTSError("Local TTS failed safely.") from exc
