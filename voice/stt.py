"""Optional faster-whisper adapter for explicit audio-file transcription.

This does not open a microphone or run an always-listening wake-word detector.
"""
from __future__ import annotations

from pathlib import Path


class STTError(RuntimeError):
    pass


class FasterWhisperSTT:
    def __init__(self, *, enabled: bool = False, model_size: str = "small",
                 device: str = "cpu", compute_type: str = "int8"):
        if model_size not in {"tiny", "base", "small", "medium", "large-v3"}:
            raise ValueError("Unsupported model size.")
        if device not in {"cpu", "cuda"}:
            raise ValueError("device must be cpu or cuda.")
        if compute_type not in {"int8", "int8_float16", "float16", "float32"}:
            raise ValueError("Unsupported compute type.")
        self.enabled = enabled
        self.model_size = model_size
        self.device = device
        self.compute_type = compute_type
        self._model = None

    def transcribe_file(self, audio_path: str | Path, *, language: str | None = "en") -> dict:
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
                self._model = WhisperModel(self.model_size, device=self.device,
                                           compute_type=self.compute_type)
            segments, info = self._model.transcribe(str(path), language=language, vad_filter=True)
            text = " ".join(segment.text.strip() for segment in segments).strip()
            return {"text": text[:20000], "language": info.language,
                    "language_probability": float(info.language_probability)}
        except ImportError as exc:
            raise STTError("Install the optional faster-whisper package to enable STT.") from exc
        except Exception as exc:
            raise STTError("Audio transcription failed safely.") from exc
