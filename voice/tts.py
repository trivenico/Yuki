"""Optional local TTS with bounded input, audio and cancellable playback."""
from __future__ import annotations

import math
import subprocess
import tempfile
import time
from pathlib import Path
from threading import Event

_MAX_CHUNKS = 100
_MAX_TOTAL_CHARACTERS = 20_000
_MAX_CHUNK_CHARACTERS = 2_000
_MAX_AUDIO_SECONDS = 120
_MAX_WAV_BYTES = 25 * 1024 * 1024


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
        engine = self._engine
        if engine is not None:
            try:
                engine.stop()
            except Exception:
                pass

    def _validated_chunks(self, chunks):
        count = 0
        total = 0
        for chunk in chunks:
            if self._interrupt_event.is_set():
                break
            if not isinstance(chunk, str):
                raise ValueError("Speech chunks must be text.")
            if not chunk.strip():
                continue
            if len(chunk) > _MAX_CHUNK_CHARACTERS:
                raise ValueError("Each speech chunk must be at most 2000 characters.")
            count += 1
            total += len(chunk)
            if count > _MAX_CHUNKS:
                raise ValueError("Speech stream exceeds the 100-chunk limit.")
            if total > _MAX_TOTAL_CHARACTERS:
                raise ValueError("Speech stream exceeds the 20000-character limit.")
            yield chunk

    def speak_stream(self, chunks):
        """Speak at most 100 chunks / 20,000 characters; stop() cancels playback."""
        if not self.enabled:
            raise TTSError("Voice output is disabled.")
        self._interrupt_event.clear()
        self._streaming = True
        spoken = 0
        try:
            for chunk in self._validated_chunks(chunks):
                if self._interrupt_event.is_set():
                    break
                self.speak(chunk)
                spoken += 1
            return {"chunks_spoken": spoken, "interrupted": self._interrupt_event.is_set()}
        finally:
            self._streaming = False

    def _play_audio(self, sd, audio, sample_rate):
        deadline = time.monotonic() + _MAX_AUDIO_SECONDS + 5
        sd.play(audio * self.volume, sample_rate)
        while True:
            if self._interrupt_event.is_set():
                sd.stop()
                raise TTSError("Speech was interrupted.")
            stream = sd.get_stream()
            if stream is None or not stream.active:
                return
            if time.monotonic() >= deadline:
                sd.stop()
                raise TTSError("Audio playback exceeded its 125-second safety limit.")
            self._interrupt_event.wait(0.05)

    def speak(self, text):
        if not self._streaming:
            self._interrupt_event.clear()
        if self._interrupt_event.is_set():
            raise TTSError("Speech was interrupted.")
        if not self.enabled:
            raise TTSError("Voice output is disabled.")
        if not isinstance(text, str) or not text.strip() or len(text) > _MAX_CHUNK_CHARACTERS:
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
                    subprocess.run(
                        [self.piper_binary, "--model", str(self.model_path),
                         "--output_file", str(output)],
                        input=text, text=True, capture_output=True,
                        timeout=30, shell=False, check=True,
                    )
                    if not output.is_file():
                        raise TTSError("Piper did not produce an audio file.")
                    info = sf.info(str(output))
                    if output.stat().st_size <= 44 or output.stat().st_size > _MAX_WAV_BYTES:
                        raise TTSError("Piper audio file is empty or exceeds the 25 MiB limit.")
                    if not 8_000 <= info.samplerate <= 96_000:
                        raise TTSError("Piper audio sample rate is outside the supported safety range.")
                    if info.channels not in (1, 2) or info.frames <= 0:
                        raise TTSError("Piper audio format is invalid.")
                    if not math.isfinite(info.duration) or info.duration <= 0 or info.duration > _MAX_AUDIO_SECONDS:
                        raise TTSError("Piper audio exceeds the 120-second duration limit.")
                    if self._interrupt_event.is_set():
                        raise TTSError("Speech was interrupted before playback.")
                    audio, sample_rate = sf.read(str(output), dtype="float32")
                    if getattr(audio, "ndim", 1) > 2:
                        raise TTSError("Piper audio has too many channels.")
                    self._play_audio(sd, audio, sample_rate)
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
            if self._interrupt_event.is_set():
                raise TTSError("Speech was interrupted.")
        except TTSError:
            raise
        except ImportError as exc:
            raise TTSError("Install optional pyttsx3 to use this backend.") from exc
        except Exception as exc:
            raise TTSError("Local TTS failed safely.") from exc
        finally:
            engine = self._engine
            self._engine = None
            if engine is not None:
                try:
                    engine.stop()
                except Exception:
                    pass
