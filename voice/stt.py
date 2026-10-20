"""Explicit recording and transcription helpers; microphone capture is opt-in and bounded."""

from __future__ import annotations
from pathlib import Path
import wave


class STTError(RuntimeError):
    pass


class FasterWhisperSTT:
    def __init__(self, *, enabled=False, model_size="small", device="cpu", compute_type="int8"):
        if model_size not in {"tiny", "base", "small", "medium", "large-v3"}:
            raise ValueError("Unsupported model size.")
        if device not in {"cpu", "cuda"}:
            raise ValueError("device must be cpu or cuda.")
        self.enabled, self.model_size, self.device, self.compute_type = (
            enabled,
            model_size,
            device,
            compute_type,
        )
        self._model = None

    def record_wav(self, output_path, *, seconds=5, sample_rate=16000):
        if not self.enabled:
            raise STTError("Speech recognition is disabled.")
        if isinstance(seconds, bool) or not isinstance(seconds, int) or not 1 <= seconds <= 20:
            raise ValueError("Recording duration must be 1-20 seconds.")
        if sample_rate not in {16000, 22050, 44100}:
            raise ValueError("Unsupported sample rate.")
        path = Path(output_path).expanduser().resolve()
        if path.exists():
            raise STTError("Refusing to overwrite an existing recording.")
        try:
            import sounddevice as sd
            import numpy as np
        except ImportError as exc:
            raise STTError("Install optional sounddevice and numpy to record audio.") from exc
        try:
            audio = sd.rec(
                int(seconds * sample_rate), samplerate=sample_rate, channels=1, dtype="int16"
            )
            sd.wait()
            path.parent.mkdir(parents=True, exist_ok=True)
            with wave.open(str(path), "wb") as handle:
                handle.setnchannels(1)
                handle.setsampwidth(2)
                handle.setframerate(sample_rate)
                handle.writeframes(np.asarray(audio, dtype=np.int16).tobytes())
            return path
        except Exception as exc:
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

                self._model = WhisperModel(
                    self.model_size, device=self.device, compute_type=self.compute_type
                )
            segments, info = self._model.transcribe(str(path), language=language, vad_filter=True)
            text = " ".join(segment.text.strip() for segment in segments).strip()
            return {
                "text": text[:20000],
                "language": info.language,
                "language_probability": float(info.language_probability),
            }
        except ImportError as exc:
            raise STTError("Install faster-whisper to enable STT.") from exc
        except Exception as exc:
            raise STTError("Audio transcription failed safely.") from exc
