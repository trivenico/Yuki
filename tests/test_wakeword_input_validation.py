"""Input-boundary tests for the optional wake-word listener."""
from __future__ import annotations

import unittest

from voice.wakeword import WakeWordDetector


class WakeWordInputValidationTests(unittest.TestCase):
    def test_listen_once_rejects_unsupported_sample_rates_before_loading_audio_deps(self):
        detector = WakeWordDetector("unused-model.onnx", enabled=True)
        detector._model = object()
        for sample_rate in (0, 8000, 96000, 1_000_000, True, 16000.0):
            with self.subTest(sample_rate=sample_rate):
                with self.assertRaisesRegex(ValueError, "sample rate"):
                    detector.listen_once(seconds=5, sample_rate=sample_rate)

    def test_listen_once_rejects_unbounded_duration(self):
        detector = WakeWordDetector("unused-model.onnx", enabled=True)
        detector._model = object()
        for seconds in (0, 21, True, 1.5):
            with self.subTest(seconds=seconds):
                with self.assertRaisesRegex(ValueError, "Listen interval"):
                    detector.listen_once(seconds=seconds, sample_rate=16000)


if __name__ == "__main__":
    unittest.main()
