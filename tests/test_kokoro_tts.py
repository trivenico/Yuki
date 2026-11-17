import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from voice.kokoro_tts import KOKORO_ENGLISH_VOICES, KokoroTTS, KokoroTTSError


class FakeAudio:
    def __init__(self, samples, sample_rate=24_000):
        self.samples = samples
        self.sample_rate = sample_rate


class FakeKokoro:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


class FakeModelConfig:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


class FakeTTS:
    def __init__(self, config):
        self.config = config
        self.generated = []
        self.is_ready = True

    def generate(self, text, config):
        self.generated.append((text, config.sid, config.speed))
        return FakeAudio([0.0] * 240)


class FakeSD:
    def __init__(self):
        self.play_calls = []
        self.stopped = 0

    def play(self, samples, sample_rate, blocking=False):
        self.play_calls.append((samples, sample_rate, blocking))

    def get_stream(self):
        return None

    def stop(self):
        self.stopped += 1


class KokoroAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.model_dir = Path(self.temp.name)
        for name in ("model.onnx", "voices.bin", "tokens.txt", "lexicon.txt"):
            (self.model_dir / name).write_bytes(b"test")
        self.sd = FakeSD()
        self.fake_tts = None

        def tts_factory(config):
            self.fake_tts = FakeTTS(config)
            return self.fake_tts

        self.sherpa = types.SimpleNamespace(
            OfflineTtsKokoroModelConfig=FakeKokoro,
            OfflineTtsModelConfig=FakeModelConfig,
            OfflineTtsConfig=FakeModelConfig,
            OfflineTts=tts_factory,
            GenerationConfig=lambda: types.SimpleNamespace(sid=0, speed=1.0),
        )
        self.patches = [
            patch.dict(sys.modules, {
                "sherpa_onnx": self.sherpa,
                "sounddevice": self.sd,
                "numpy": __import__("numpy"),
            })
        ]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)

    def make(self, **kwargs):
        return KokoroTTS(model_dir=self.model_dir, **kwargs)

    def test_construction_is_lazy_and_disabled(self):
        adapter = self.make()
        self.assertFalse(adapter.enabled)
        self.assertIsNone(adapter._tts)
        self.assertEqual(self.sd.play_calls, [])

    def test_invalid_voice_and_ranges_rejected(self):
        with self.assertRaises(ValueError):
            self.make(voice="unknown")
        with self.assertRaises(ValueError):
            self.make(speed=0.1)
        with self.assertRaises(ValueError):
            self.make(num_threads=0)

    def test_voice_ids_match_documented_presets(self):
        self.assertEqual(KOKORO_ENGLISH_VOICES["af_bella"], 1)
        self.assertEqual(KOKORO_ENGLISH_VOICES["bf_emma"], 7)

    def test_enabled_speech_loads_model_lazily_and_uses_voice_speed(self):
        adapter = self.make(enabled=True, voice="bf_emma", speed=1.1)
        adapter.speak("Hello Yuki.")
        self.assertEqual(self.fake_tts.generated, [("Hello Yuki.", 7, 1.1)])
        self.assertEqual(len(self.sd.play_calls), 1)
        self.assertEqual(self.sd.play_calls[0][1], 24_000)

    def test_missing_model_files_fail_closed(self):
        (self.model_dir / "tokens.txt").unlink()
        adapter = self.make(enabled=True)
        with self.assertRaisesRegex(KokoroTTSError, "incomplete"):
            adapter.speak("Hello.")

    def test_missing_lexicon_fails_closed(self):
        adapter = self.make(enabled=True, lexicon_path=self.model_dir / "missing.txt")
        with self.assertRaisesRegex(KokoroTTSError, "lexicon"):
            adapter.speak("Hello.")

    def test_disabled_speech_never_loads_or_plays(self):
        adapter = self.make(enabled=False)
        with self.assertRaisesRegex(KokoroTTSError, "disabled"):
            adapter.speak("Hello.")
        self.assertIsNone(adapter._tts)
        self.assertEqual(self.sd.play_calls, [])

    def test_stream_validates_all_chunks_before_audio(self):
        adapter = self.make(enabled=True)
        with self.assertRaisesRegex(ValueError, "must be text"):
            adapter.speak_stream(["first", object()])
        self.assertIsNone(adapter._tts)
        self.assertEqual(self.sd.play_calls, [])

    def test_stream_bounds_and_speaks(self):
        adapter = self.make(enabled=True)
        result = adapter.speak_stream(["Hello", "", "Yuki"])
        self.assertEqual(result, {"chunks_spoken": 2, "interrupted": False})
        self.assertEqual(len(self.fake_tts.generated), 2)

    def test_output_audio_bounds_are_enforced_before_playback(self):
        adapter = self.make(enabled=True)
        with patch.object(adapter, "_load", return_value=FakeTTS(None)):
            with patch.object(adapter._load(), "generate", return_value=FakeAudio([float("nan")])):
                with self.assertRaisesRegex(KokoroTTSError, "non-finite"):
                    adapter.speak("Hello.")
        self.assertEqual(self.sd.play_calls, [])

    def test_stop_stops_audio_device_and_close_releases_model(self):
        adapter = self.make(enabled=True)
        adapter._tts = object()
        adapter.stop()
        self.assertGreaterEqual(self.sd.stopped, 1)
        adapter.close()
        self.assertIsNone(adapter._tts)


if __name__ == "__main__":
    unittest.main()
