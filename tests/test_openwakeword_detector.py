import tempfile
import threading
import unittest
from pathlib import Path

try:
    import numpy  # noqa: F401
except ImportError:
    numpy = None

from voice.openwakeword_detector import (
    OpenWakeWordDetector,
    OpenWakeWordDetectorError,
)
from voice.openwakeword_session import create_openwakeword_session


class FakeModel:
    def __init__(self, scores, key="hey_yuki"):
        self.scores = list(scores)
        self.key = key
        self.calls = 0
        self.reset_called = False

    def predict(self, audio):
        self.calls += 1
        score = self.scores.pop(0) if self.scores else 0.0
        return {self.key: score}

    def reset(self):
        self.reset_called = True


class FakeRawInputStream:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.callback = kwargs["callback"]
        self.stopped = False
        self.closed = False

    def start(self):
        pass

    def emit(self, data):
        self.callback(data, len(data) // 2, None, None)

    def stop(self):
        self.stopped = True

    def close(self):
        self.closed = True


class FakeSoundDevice:
    def __init__(self):
        self.streams = []

    def RawInputStream(self, **kwargs):
        stream = FakeRawInputStream(**kwargs)
        self.streams.append(stream)
        return stream


@unittest.skipIf(numpy is None, "NumPy is an optional wake-word dependency")
class OpenWakeWordDetectorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.model_path = Path(self.temp.name) / "hey_yuki.onnx"
        self.model_path.write_bytes(b"fake-onnx-model")

    def tearDown(self):
        self.temp.cleanup()

    def test_loads_only_explicit_onnx_model_and_requires_consecutive_frames(self):
        fake = FakeModel([0.9, 0.4, 0.9, 0.95])
        calls = []

        def factory(**kwargs):
            calls.append(kwargs)
            return fake

        detector = OpenWakeWordDetector(
            model_path=self.model_path,
            threshold=0.8,
            trigger_frames=2,
            model_factory=factory,
        )
        self.assertEqual(calls, [{
            "wakeword_models": [str(self.model_path)],
            "inference_framework": "onnx",
        }])
        frame = bytes(OpenWakeWordDetector.FRAME_LENGTH * 2)
        self.assertFalse(detector.process(frame))
        self.assertFalse(detector.process(frame))
        self.assertFalse(detector.process(frame))
        self.assertTrue(detector.process(frame))
        detector.close()
        self.assertTrue(fake.reset_called)

    def test_rejects_bad_frame_and_bad_prediction(self):
        fake = FakeModel([float("nan")])
        detector = OpenWakeWordDetector(
            model_path=self.model_path,
            model_factory=lambda **kwargs: fake,
        )
        with self.assertRaises(ValueError):
            detector.process(b"short")
        with self.assertRaises(OpenWakeWordDetectorError):
            detector.process(bytes(OpenWakeWordDetector.FRAME_LENGTH * 2))
        detector.close()

    def test_invalid_model_path_and_settings_are_rejected_before_loading(self):
        calls = []
        factory = lambda **kwargs: calls.append(kwargs)
        with self.assertRaises(ValueError):
            OpenWakeWordDetector(
                model_path=str(self.model_path) + ".missing",
                model_factory=factory,
            )
        with self.assertRaises(ValueError):
            OpenWakeWordDetector(
                model_path=self.model_path,
                threshold=0,
                model_factory=factory,
            )
        self.assertEqual(calls, [])

    def test_close_is_idempotent_and_process_after_close_fails(self):
        fake = FakeModel([0.0])
        detector = OpenWakeWordDetector(
            model_path=self.model_path,
            model_factory=lambda **kwargs: fake,
        )
        detector.close()
        detector.close()
        with self.assertRaises(OpenWakeWordDetectorError):
            detector.process(bytes(OpenWakeWordDetector.FRAME_LENGTH * 2))


@unittest.skipIf(numpy is None, "NumPy is an optional wake-word dependency")
class OpenWakeWordSessionTests(unittest.TestCase):
    def test_factory_is_lazy_and_matches_80ms_audio_contract(self):
        with tempfile.TemporaryDirectory() as temp:
            model_path = Path(temp) / "hey_yuki.onnx"
            model_path.write_bytes(b"fake-model")
            fake = FakeModel([0.9, 0.9])
            sounddevice = FakeSoundDevice()
            woke = threading.Event()
            session = create_openwakeword_session(
                model_path=str(model_path),
                on_wake=woke.set,
                threshold=0.8,
                trigger_frames=2,
                model_factory=lambda **kwargs: fake,
                sounddevice_module=sounddevice,
            )
            self.assertEqual(sounddevice.streams, [])
            self.assertEqual(fake.calls, 0)
            session.start()
            stream = sounddevice.streams[0]
            self.assertEqual(stream.kwargs["samplerate"], 16000)
            self.assertEqual(stream.kwargs["blocksize"], 1280)
            frame = bytes(1280 * 2)
            stream.emit(frame)
            for _ in range(100):
                if fake.calls >= 1:
                    break
                threading.Event().wait(0.01)
            self.assertEqual(fake.calls, 1)
            stream.emit(frame)
            self.assertTrue(woke.wait(2))
            self.assertTrue(session.stop())
            self.assertTrue(stream.closed)
            self.assertTrue(fake.reset_called)


if __name__ == "__main__":
    unittest.main()
