import tempfile
import threading
import unittest
from pathlib import Path

from voice.porcupine_detector import (
    PorcupineDetectorError,
    PorcupineWakeWordDetector,
)
from voice.porcupine_session import create_porcupine_wakeword_session


class FakePorcupineEngine:
    def __init__(self, *, result=0, sample_rate=16000, frame_length=4):
        self.result = result
        self.sample_rate = sample_rate
        self.frame_length = frame_length
        self.frames = []
        self.deleted = False

    def process(self, pcm):
        self.frames.append(pcm)
        return self.result

    def delete(self):
        self.deleted = True


class FakePorcupineModule:
    def __init__(self, engine=None):
        self.engine = engine or FakePorcupineEngine()
        self.create_calls = []

    def create(self, **kwargs):
        self.create_calls.append(kwargs)
        return self.engine


class FakeRawInputStream:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.callback = kwargs["callback"]
        self.started = False
        self.stopped = False
        self.closed = False

    def start(self):
        self.started = True

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


class PorcupineDetectorTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.model_path = Path(self.temp_dir.name) / "hey-yuki.ppn"
        self.model_path.write_bytes(b"test-model")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_model_is_loaded_only_when_detector_is_constructed(self):
        engine = FakePorcupineEngine()
        module = FakePorcupineModule(engine)
        detector = PorcupineWakeWordDetector(
            access_key="test-access-key",
            keyword_model_path=str(self.model_path),
            porcupine_module=module,
        )
        self.assertEqual(len(module.create_calls), 1)
        self.assertEqual(module.create_calls[0]["keyword_paths"], [str(self.model_path)])
        self.assertTrue(detector.process(bytes([1, 0, 2, 0, 3, 0, 4, 0])))
        self.assertEqual(engine.frames, [[1, 2, 3, 4]])
        detector.close()
        detector.close()
        self.assertTrue(engine.deleted)

    def test_no_detection_returns_false(self):
        engine = FakePorcupineEngine(result=-1)
        detector = PorcupineWakeWordDetector(
            access_key="test-access-key",
            keyword_model_path=str(self.model_path),
            porcupine_module=FakePorcupineModule(engine),
        )
        self.assertFalse(detector.process(bytes(8)))
        detector.close()

    def test_invalid_frame_and_closed_engine_are_rejected(self):
        detector = PorcupineWakeWordDetector(
            access_key="test-access-key",
            keyword_model_path=str(self.model_path),
            porcupine_module=FakePorcupineModule(),
        )
        with self.assertRaises(ValueError):
            detector.process(b"short")
        detector.close()
        with self.assertRaises(PorcupineDetectorError):
            detector.process(bytes(8))

    def test_invalid_access_key_and_model_are_rejected(self):
        module = FakePorcupineModule()
        with self.assertRaises(ValueError):
            PorcupineWakeWordDetector(
                access_key=" ",
                keyword_model_path=str(self.model_path),
                porcupine_module=module,
            )
        with self.assertRaises(ValueError):
            PorcupineWakeWordDetector(
                access_key="test-access-key",
                keyword_model_path=str(self.model_path) + ".missing",
                porcupine_module=module,
            )
        self.assertEqual(module.create_calls, [])

    def test_session_factory_is_lazy_and_matches_engine_audio_format(self):
        engine = FakePorcupineEngine()
        porcupine = FakePorcupineModule(engine)
        sounddevice = FakeSoundDevice()
        woke = threading.Event()
        with create_porcupine_wakeword_session(
            access_key="test-access-key",
            keyword_model_path=str(self.model_path),
            on_wake=woke.set,
            porcupine_module=porcupine,
            sounddevice_module=sounddevice,
        ) as session:
            self.assertEqual(porcupine.create_calls, [])
            self.assertEqual(sounddevice.streams, [])
            session.start()
            self.assertEqual(len(porcupine.create_calls), 1)
            stream = sounddevice.streams[0]
            self.assertEqual(stream.kwargs["samplerate"], 16000)
            self.assertEqual(stream.kwargs["blocksize"], 4)
            stream.emit(bytes([1, 0, 2, 0, 3, 0, 4, 0]))
            self.assertTrue(woke.wait(2))
            self.assertEqual(engine.frames, [[1, 2, 3, 4]])
            self.assertTrue(session.stop())
        self.assertTrue(engine.deleted)


if __name__ == "__main__":
    unittest.main()
