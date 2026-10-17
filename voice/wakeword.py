"""Explicit openWakeWord adapter; custom model training is a separate data/training task."""
from pathlib import Path


class WakeWordError(RuntimeError):
    pass


class WakeWordDetector:
    def __init__(self, model_path, *, enabled=False, threshold=0.65):
        if isinstance(threshold, bool) or not isinstance(threshold, (int, float)) or not 0.1 <= threshold <= 0.99:
            raise ValueError("threshold must be between 0.1 and 0.99")
        self.model_path = Path(model_path).expanduser().resolve()
        self.enabled, self.threshold, self._model = enabled, float(threshold), None

    def load(self):
        if not self.enabled:
            raise WakeWordError("Wake-word detection is disabled.")
        if not self.model_path.is_file() or self.model_path.stat().st_size > 100 * 1024 * 1024:
            raise WakeWordError("Provide a local detector model file up to 100 MiB.")
        try:
            from openwakeword.model import Model
            self._model = Model(wakeword_models=[str(self.model_path)], inference_framework="onnx")
        except ImportError as exc:
            raise WakeWordError("Install optional openwakeword dependencies first.") from exc
        except Exception as exc:
            raise WakeWordError("Could not load the wake-word model.") from exc

    def detect_scores(self, audio_frame):
        if not self.enabled or self._model is None:
            raise WakeWordError("Load the enabled detector before scoring audio.")
        try:
            scores = self._model.predict(audio_frame)
            return {str(name): float(score) for name, score in scores.items()
                    if float(score) >= self.threshold}
        except Exception as exc:
            raise WakeWordError("Wake-word inference failed.") from exc

    def listen_once(self, *, seconds=5, sample_rate=16000):
        """Listen only for the requested finite interval; no background thread/service."""
        if not self.enabled or self._model is None:
            raise WakeWordError("Load the enabled detector before listening.")
        if isinstance(seconds, bool) or not isinstance(seconds, int) or not 1 <= seconds <= 20:
            raise ValueError("Listen interval must be 1-20 seconds.")
        try:
            import numpy as np
            import sounddevice as sd
        except ImportError as exc:
            raise WakeWordError("Install optional numpy and sounddevice to listen.") from exc
        try:
            audio = sd.rec(seconds * sample_rate, samplerate=sample_rate, channels=1, dtype="int16")
            sd.wait()
            detected = {}
            frame_size = 1280
            flattened = np.asarray(audio, dtype=np.int16).reshape(-1)
            for start in range(0, len(flattened) - frame_size + 1, frame_size):
                scores = self._model.predict(flattened[start:start + frame_size])
                for name, score in scores.items():
                    if float(score) >= self.threshold:
                        detected[str(name)] = float(score)
            return {"detected": bool(detected), "scores": detected, "seconds_listened": seconds}
        except Exception as exc:
            raise WakeWordError("Finite wake-word listening failed safely.") from exc

    @staticmethod
    def training_guidance():
        return ("Collect consented positive examples of the chosen wake phrase and diverse negative/background "
                "samples; train/export using the supported openWakeWord workflow, evaluate false accepts/rejects, "
                "then load the resulting local model. This module does not train the model.")
