"""Opt-in openWakeWord adapter for an explicitly supplied detector model.

This module does not train a model. Custom model training requires suitable audio
examples and the openWakeWord training workflow; this adapter loads the resulting
model file. Listening is always an explicit caller action.
"""
from __future__ import annotations

from pathlib import Path


class WakeWordError(RuntimeError):
    pass


class WakeWordDetector:
    def __init__(self, model_path: str | Path, *, enabled: bool = False, threshold: float = 0.65):
        if not isinstance(threshold, (int, float)) or isinstance(threshold, bool) or not 0.1 <= threshold <= 0.99:
            raise ValueError("threshold must be between 0.1 and 0.99")
        self.model_path = Path(model_path).expanduser().resolve()
        self.enabled = enabled
        self.threshold = float(threshold)
        self._model = None

    def load(self) -> None:
        if not self.enabled:
            raise WakeWordError("Wake-word detection is disabled.")
        if not self.model_path.is_file() or self.model_path.stat().st_size > 100 * 1024 * 1024:
            raise WakeWordError("Provide a valid local wake-word model file (maximum 100 MiB).")
        try:
            from openwakeword.model import Model
            self._model = Model(wakeword_models=[str(self.model_path)], inference_framework="onnx")
        except ImportError as exc:
            raise WakeWordError("Install optional openwakeword dependencies first.") from exc
        except Exception as exc:
            raise WakeWordError("Could not load the wake-word model.") from exc

    def predict(self, audio_frame) -> bool:
        if not self.enabled or self._model is None:
            raise WakeWordError("Load an enabled detector before prediction.")
        try:
            scores = self._model.predict(audio_frame)
            return any(float(score) >= self.threshold for score in scores.values())
        except Exception as exc:
            raise WakeWordError("Wake-word prediction failed.") from exc
