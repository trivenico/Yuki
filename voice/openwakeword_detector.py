"""Optional local openWakeWord ONNX detector adapter."""
from __future__ import annotations

import math
from pathlib import Path
from threading import Lock


class OpenWakeWordDetectorError(RuntimeError):
    """The local openWakeWord detector could not be initialized or used."""


class OpenWakeWordDetector:
    """Load one explicit user-supplied ONNX wake model; never download models."""

    SAMPLE_RATE = 16000
    FRAME_LENGTH = 1280  # 80 ms, as required by the openWakeWord feature pipeline
    MAX_MODEL_BYTES = 64 * 1024 * 1024

    def __init__(
        self,
        *,
        model_path: str | Path,
        threshold: float = 0.5,
        trigger_frames: int = 2,
        model_factory=None,
    ) -> None:
        if isinstance(threshold, bool) or not isinstance(threshold, (int, float)) or not 0.0 < threshold <= 1.0:
            raise ValueError("threshold must be a number greater than 0 and at most 1.")
        if isinstance(trigger_frames, bool) or not isinstance(trigger_frames, int) or not 1 <= trigger_frames <= 10:
            raise ValueError("trigger_frames must be an integer between 1 and 10.")

        requested = Path(model_path).expanduser()
        if requested.suffix.lower() != ".onnx" or requested.is_symlink():
            raise ValueError("A regular user-supplied .onnx wake-word model is required.")
        try:
            resolved = requested.resolve(strict=True)
            if not resolved.is_file() or resolved.stat().st_size <= 0 or resolved.stat().st_size > self.MAX_MODEL_BYTES:
                raise ValueError("The ONNX wake-word model is missing, empty, or too large.")
        except OSError as exc:
            raise ValueError("The ONNX wake-word model is unavailable.") from exc

        if model_factory is None:
            try:
                from openwakeword.model import Model
            except ImportError as exc:
                raise OpenWakeWordDetectorError(
                    "Install the optional openwakeword and onnxruntime dependencies."
                ) from exc
            model_factory = Model

        try:
            model = model_factory(
                wakeword_models=[str(resolved)],
                inference_framework="onnx",
            )
        except Exception as exc:
            raise OpenWakeWordDetectorError(
                "openWakeWord could not load the supplied local ONNX model."
            ) from exc

        self.model_key = resolved.stem
        self.sample_rate = self.SAMPLE_RATE
        self.frame_length = self.FRAME_LENGTH
        self._model = model
        self._threshold = float(threshold)
        self._trigger_frames = trigger_frames
        self._consecutive = 0
        self._lock = Lock()
        self._closed = False

    def process(self, frame: bytes) -> bool:
        if not isinstance(frame, (bytes, bytearray, memoryview)):
            raise TypeError("PCM input must be bytes-like.")
        if len(frame) != self.FRAME_LENGTH * 2:
            raise ValueError("PCM frame must contain exactly 1280 little-endian PCM16 samples.")

        try:
            import numpy as np
        except ImportError as exc:
            raise OpenWakeWordDetectorError("NumPy is required by the openWakeWord adapter.") from exc

        audio = np.frombuffer(frame, dtype="<i2")
        with self._lock:
            if self._closed or self._model is None:
                raise OpenWakeWordDetectorError("The wake-word detector is closed.")
            try:
                predictions = self._model.predict(audio)
            except Exception as exc:
                raise OpenWakeWordDetectorError("Local openWakeWord inference failed.") from exc

            if not isinstance(predictions, dict) or self.model_key not in predictions:
                raise OpenWakeWordDetectorError("The model returned no score for its configured model key.")
            try:
                score = float(predictions[self.model_key])
            except (TypeError, ValueError, OverflowError) as exc:
                raise OpenWakeWordDetectorError("The model returned an invalid prediction score.") from exc
            if not math.isfinite(score) or not 0.0 <= score <= 1.0:
                raise OpenWakeWordDetectorError("The model returned an out-of-range prediction score.")

            if score >= self._threshold:
                self._consecutive += 1
            else:
                self._consecutive = 0
            if self._consecutive >= self._trigger_frames:
                self._consecutive = 0
                return True
            return False

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            model = self._model
            self._model = None
            self._consecutive = 0
        reset = getattr(model, "reset", None)
        if callable(reset):
            try:
                reset()
            except Exception:
                pass
        del model
