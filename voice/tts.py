"""Optional local Piper TTS adapter. Voice is disabled unless explicitly enabled."""
from __future__ import annotations
import subprocess
from pathlib import Path


class TTSError(RuntimeError):
    pass


class LocalTTS:
    def __init__(self, *, enabled=False, rate=170, volume=1.0, backend="piper",
                 model_path=None, piper_binary="piper"):
        if isinstance(rate, bool) or not isinstance(rate, int) or not 80 <= rate <= 260:
            raise ValueError("rate must be between 80 and 260.")
        if not isinstance(volume, (int, float)) or isinstance(volume, bool) or not 0 <= volume <= 1:
            raise ValueError("volume must be between 0 and 1.")
        if backend not in {"piper", "pyttsx3"}:
            raise ValueError("backend must be piper or pyttsx3.")
        self.enabled, self.rate, self.volume = enabled, rate, float(volume)
        self.backend = backend
        self.model_path = Path(model_path).expanduser().resolve() if model_path else None
        self.piper_binary = piper_binary

    def speak(self, text):
        if not self.enabled:
            raise TTSError("Voice output is disabled.")
        if not isinstance(text, str) or not text.strip() or len(text) > 2000:
            raise ValueError("Text must be 1-2000 characters.")
        if self.backend == "piper":
            if self.model_path is None or not self.model_path.is_file():
                raise TTSError("Configure a local Piper voice model file in settings.")
            try:
                subprocess.run([self.piper_binary, "--model", str(self.model_path)], input=text,
                               text=True, capture_output=True, timeout=30, shell=False, check=True)
            except FileNotFoundError as exc:
                raise TTSError("Piper executable was not found. Install it separately.") from exc
            except subprocess.TimeoutExpired as exc:
                raise TTSError("Piper speech timed out.") from exc
            except subprocess.CalledProcessError as exc:
                raise TTSError("Piper speech generation failed.") from exc
            return
        try:
            import pyttsx3
            engine = pyttsx3.init()
            engine.setProperty("rate", self.rate); engine.setProperty("volume", self.volume)
            engine.say(text); engine.runAndWait(); engine.stop()
        except ImportError as exc:
            raise TTSError("Install optional pyttsx3 to use this backend.") from exc
        except Exception as exc:
            raise TTSError("Local TTS failed safely.") from exc
