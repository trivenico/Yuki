"""Optional local Piper TTS adapter with chunked speech and best-effort interruption."""
from __future__ import annotations
import subprocess
import tempfile
from pathlib import Path
from threading import Event


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
        self._interrupt_event = Event()
        self._streaming = False
        self._engine = None

    def stop(self):
        """Best-effort immediate interruption of active playback."""
        self._interrupt_event.set()
        try:
            import sounddevice as sd
            sd.stop()
        except Exception:
            pass
        if self._engine is not None:
            try:
                self._engine.stop()
            except Exception:
                pass

    def speak_stream(self, chunks):
        """Speak bounded text chunks sequentially; stop() interrupts current playback."""
        if not self.enabled:
            raise TTSError("Voice output is disabled.")
        self._interrupt_event.clear()
        self._streaming = True
        spoken = 0
        try:
            for chunk in chunks:
                if self._interrupt_event.is_set():
                    break
                if not isinstance(chunk, str) or not chunk.strip():
                    continue
                if len(chunk) > 2000:
                    raise ValueError("Each speech chunk must be at most 2000 characters.")
                self.speak(chunk)
                spoken += 1
            return {"chunks_spoken": spoken, "interrupted": self._interrupt_event.is_set()}
        finally:
            self._streaming = False

    def speak(self, text):
        if not self._streaming:
            self._interrupt_event.clear()
        if self._interrupt_event.is_set():
            raise TTSError("Speech was interrupted.")
        if not self.enabled:
            raise TTSError("Voice output is disabled.")
        if not isinstance(text, str) or not text.strip() or len(text) > 2000:
            raise ValueError("Text must be 1-2000 characters.")
        if self.backend == "piper":
            if self.model_path is None or not self.model_path.is_file():
                raise TTSError("Configure a local Piper voice model file in settings.")
            try:
                import sounddevice as sd
                import soundfile as sf
            except ImportError as exc:
                raise TTSError("Install optional sounddevice and soundfile to play Piper audio.") from exc
            try:
                with tempfile.TemporaryDirectory(prefix="yuki-tts-") as directory:
                    output = Path(directory) / "speech.wav"
                    subprocess.run([self.piper_binary, "--model", str(self.model_path),
                                    "--output_file", str(output)], input=text, text=True,
                                   capture_output=True, timeout=30, shell=False, check=True)
                    if not output.is_file() or output.stat().st_size <= 44:
                        raise TTSError("Piper did not produce a valid audio file.")
                    if self._interrupt_event.is_set():
                        raise TTSError("Speech was interrupted before playback.")
                    audio, sample_rate = sf.read(str(output), dtype="float32")
                    sd.play(audio * self.volume, sample_rate)
                    sd.wait()
                    if self._interrupt_event.is_set():
                        raise TTSError("Speech was interrupted.")
            except TTSError:
                raise
            except FileNotFoundError as exc:
                raise TTSError("Piper executable was not found. Install it separately.") from exc
            except subprocess.TimeoutExpired as exc:
                raise TTSError("Piper speech generation timed out.") from exc
            except subprocess.CalledProcessError as exc:
                raise TTSError("Piper speech generation failed.") from exc
            except Exception as exc:
                raise TTSError("Piper playback failed safely.") from exc
            return
        try:
            import pyttsx3
            engine = pyttsx3.init()
            self._engine = engine
            engine.setProperty("rate", self.rate)
            engine.setProperty("volume", self.volume)
            engine.say(text)
            engine.runAndWait()
            engine.stop()
            self._engine = None
            if self._interrupt_event.is_set():
                raise TTSError("Speech was interrupted.")
        except TTSError:
            raise
        except ImportError as exc:
            raise TTSError("Install optional pyttsx3 to use this backend.") from exc
        except Exception as exc:
            raise TTSError("Local TTS failed safely.") from exc
