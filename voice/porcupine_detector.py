"""Optional Picovoice Porcupine detector adapter.

The SDK, AccessKey and keyword model are never bundled or downloaded by Yuki.
Construction is intended to happen only inside an explicitly started wake-word
session's detector factory.
"""
from __future__ import annotations

from array import array
from pathlib import Path
import sys
from threading import Lock


class PorcupineDetectorError(RuntimeError):
    """A local Porcupine detector could not be initialized or used safely."""


class PorcupineWakeWordDetector:
    """Adapt one user-provided Porcupine .ppn keyword model to Yuki's contract."""

    MAX_KEYWORD_MODEL_BYTES = 25 * 1024 * 1024

    def __init__(
        self,
        *,
        access_key: str,
        keyword_model_path: str | Path,
        sensitivity: float = 0.5,
        porcupine_module=None,
    ) -> None:
        if not isinstance(access_key, str) or not access_key or access_key != access_key.strip():
            raise ValueError("A valid Picovoice AccessKey is required.")
        if len(access_key) > 4096 or any(ch in access_key for ch in "\r\n\x00"):
            raise ValueError("The Picovoice AccessKey has an invalid format.")
        if isinstance(sensitivity, bool) or not isinstance(sensitivity, (int, float)) or not 0.0 <= sensitivity <= 1.0:
            raise ValueError("sensitivity must be a number between 0 and 1.")

        requested = Path(keyword_model_path).expanduser()
        if requested.suffix.lower() != ".ppn" or requested.is_symlink():
            raise ValueError("A regular, user-provided .ppn keyword model is required.")
        try:
            model_path = requested.resolve(strict=True)
            if not model_path.is_file() or model_path.stat().st_size > self.MAX_KEYWORD_MODEL_BYTES:
                raise ValueError("The keyword model is missing or exceeds the size limit.")
        except OSError as exc:
            raise ValueError("The keyword model file is unavailable.") from exc

        module = porcupine_module
        if module is None:
            try:
                import pvporcupine as module
            except ImportError as exc:
                raise PorcupineDetectorError(
                    "Install the optional pvporcupine package to use this wake-word model."
                ) from exc

        try:
            engine = module.create(
                access_key=access_key,
                keyword_paths=[str(model_path)],
                sensitivities=[float(sensitivity)],
            )
        except Exception as exc:
            # Do not include SDK exception text: it may contain provider details.
            raise PorcupineDetectorError(
                "Porcupine could not initialize the supplied keyword model."
            ) from exc

        sample_rate = getattr(engine, "sample_rate", None)
        frame_length = getattr(engine, "frame_length", None)
        if (
            isinstance(sample_rate, bool)
            or not isinstance(sample_rate, int)
            or not 8000 <= sample_rate <= 96000
            or isinstance(frame_length, bool)
            or not isinstance(frame_length, int)
            or not 1 <= frame_length <= 8192
        ):
            try:
                engine.delete()
            except Exception:
                pass
            raise PorcupineDetectorError("Porcupine returned an unsupported audio format.")

        self.sample_rate = sample_rate
        self.frame_length = frame_length
        self._engine = engine
        self._lock = Lock()
        self._closed = False

    def process(self, frame: bytes) -> bool:
        if not isinstance(frame, (bytes, bytearray, memoryview)):
            raise TypeError("PCM input must be bytes-like.")
        if len(frame) != self.frame_length * 2:
            raise ValueError("PCM frame length does not match the detector model.")
        samples = array("h")
        samples.frombytes(bytes(frame))
        if sys.byteorder != "little":
            samples.byteswap()

        with self._lock:
            if self._closed or self._engine is None:
                raise PorcupineDetectorError("The wake-word detector is closed.")
            try:
                result = self._engine.process(samples.tolist())
            except Exception as exc:
                raise PorcupineDetectorError("Local wake-word inference failed.") from exc

        if type(result) is not int or result < -1 or result > 0:
            raise PorcupineDetectorError("Porcupine returned an invalid keyword index.")
        return result == 0

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            engine = self._engine
            self._engine = None
        if engine is not None:
            try:
                engine.delete()
            except Exception:
                pass
