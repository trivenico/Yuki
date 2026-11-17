"""Explicit recording and transcription helpers; microphone capture is opt-in and bounded."""
from __future__ import annotations
from pathlib import Path
import os
import wave
import time


class STTError(RuntimeError):
    pass


class FasterWhisperSTT:
    def __init__(self, *, enabled=False, model_size="small", device="cpu", compute_type="int8"):
        if model_size not in {"tiny", "base", "small", "medium", "large-v3"}:
            raise ValueError("Unsupported model size.")
        if device not in {"cpu", "cuda"}:
            raise ValueError("device must be cpu or cuda.")
        self.enabled, self.model_size, self.device, self.compute_type = enabled, model_size, device, compute_type
        self._model = None

    def record_wav(self, output_path, *, seconds=5, sample_rate=16000, cancellation_event=None):
        if not self.enabled:
            raise STTError("Speech recognition is disabled.")
        if isinstance(seconds, bool) or not isinstance(seconds, int) or not 1 <= seconds <= 20:
            raise ValueError("Recording duration must be 1-20 seconds.")
        if isinstance(sample_rate, bool) or not isinstance(sample_rate, int) or sample_rate not in {16000, 22050, 44100}:
            raise ValueError("Unsupported sample rate.")
        requested_path = Path(output_path).expanduser()
        if not requested_path.name or requested_path.name in {".", ".."}:
            raise ValueError("A recording file path is required.")
        try:
            import sounddevice as sd
            import numpy as np
        except ImportError as exc:
            raise STTError("Install optional sounddevice and numpy to record audio.") from exc

        # Resolve only the parent, not the final component: exclusive creation
        # rejects existing files, symlinks and races without following them.
        try:
            requested_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            path = requested_path.parent.resolve() / requested_path.name
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            descriptor = os.open(path, flags, 0o600)
        except FileExistsError as exc:
            raise STTError("Refusing to overwrite an existing recording.") from exc
        except OSError as exc:
            raise STTError("Could not securely create the recording file.") from exc

        try:
            if cancellation_event is not None and not callable(getattr(cancellation_event, "is_set", None)):
                raise ValueError("cancellation_event must provide is_set().")
            if cancellation_event is not None and cancellation_event.is_set():
                raise STTError("Recording was cancelled.")
            audio = sd.rec(int(seconds * sample_rate), samplerate=sample_rate, channels=1, dtype="int16")
            if cancellation_event is None:
                sd.wait()
            else:
                stream = sd.get_stream()
                while stream.active:
                    if cancellation_event.is_set():
                        sd.stop()
                        raise STTError("Recording was cancelled.")
                    time.sleep(0.05)
                sd.wait()
                if cancellation_event.is_set():
                    raise STTError("Recording was cancelled.")
            with os.fdopen(descriptor, "wb") as output:
                descriptor = -1
                with wave.open(output, "wb") as handle:
                    handle.setnchannels(1)
                    handle.setsampwidth(2)
                    handle.setframerate(sample_rate)
                    handle.writeframes(np.asarray(audio, dtype=np.int16).tobytes())
            return path
        except STTError:
            if descriptor >= 0:
                os.close(descriptor)
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
            raise
        except Exception as exc:
            if descriptor >= 0:
                os.close(descriptor)
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
            raise STTError("Recording failed; check microphone permission and device.") from exc

    def transcribe_file(self, audio_path, *, language=None):
        if not self.enabled:
            raise STTError("Speech recognition is disabled.")
        path = Path(audio_path).expanduser().resolve()
        if not path.is_file() or path.stat().st_size <= 0:
            raise STTError("Provide an existing, non-empty audio file.")
        if path.stat().st_size > 100 * 1024 * 1024:
            raise STTError("Audio file exceeds the 100 MiB limit.")
        if language is not None and (not isinstance(language, str) or len(language) not in {2, 3}):
            raise ValueError("language must be a short language code or None.")
        try:
            if self._model is None:
                from faster_whisper import WhisperModel
                self._model = WhisperModel(self.model_size, device=self.device, compute_type=self.compute_type)
            segments, info = self._model.transcribe(str(path), language=language, vad_filter=True)
            text = " ".join(segment.text.strip() for segment in segments).strip()
            return {"text": text[:20000], "language": info.language,
                    "language_probability": float(info.language_probability)}
        except ImportError as exc:
            raise STTError("Install faster-whisper to enable STT.") from exc
        except Exception as exc:
            raise STTError("Audio transcription failed safely.") from exc
