import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from voice.desktop_output import create_desktop_voice_output
from voice.profile import DEFAULT_VOICE_PROFILE
from voice.profiled_tts import ProfiledLocalSpeechOutput


class FakeLocalTTS:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.enabled = kwargs["enabled"]
        self.rate = kwargs.get("rate", 170)
        self.backend = kwargs.get("backend", "kokoro")
        self.speed = kwargs.get("speed", 1.0)
        self.speed = kwargs.get("speed", 1.0)
        self.calls = []

    def speak(self, text):
        self.calls.append(text)

    def speak_stream(self, chunks):
        self.calls.extend(list(chunks))

    def stop(self):
        pass


class DesktopVoiceOutputFactoryTests(unittest.TestCase):
    def test_piper_requires_existing_model_and_runtime(self):
        with self.assertRaisesRegex(ValueError, "existing local Piper"):
            create_desktop_voice_output({"tts_backend": "piper"})

    def test_factory_is_disabled_and_uses_default_voice_profile(self):
        with tempfile.TemporaryDirectory() as directory:
            model = Path(directory) / "voice.onnx"
            model.write_bytes(b"model")
            with (
                patch("voice.tts.LocalTTS", FakeLocalTTS),
                patch("voice.desktop_output.shutil.which", return_value="/usr/bin/piper"),
                patch("voice.desktop_output.importlib.util.find_spec", return_value=object()),
            ):
                session = create_desktop_voice_output({
                    "tts_backend": "piper",
                    "tts_rate": 180,
                    "piper_model_path": str(model),
                })
        self.assertFalse(session.enabled)
        self.assertIsInstance(session._synthesizer, ProfiledLocalSpeechOutput)
        self.assertEqual(session._synthesizer.profile.profile_id, DEFAULT_VOICE_PROFILE.profile_id)
        self.assertEqual(session._synthesizer.profile.speaking_rate, 180)
        self.assertFalse(session._synthesizer.enabled)
        self.assertFalse(session._synthesizer.synthesizer.enabled)
        self.assertEqual(session._synthesizer.synthesizer.kwargs["backend"], "piper")
        self.assertEqual(session._synthesizer.synthesizer.kwargs["rate"], 180)
        self.assertEqual(session._synthesizer.capability_report()["unsupported_controls"],
                         ["expressiveness", "pitch", "speaking_rate", "style"])
        self.assertEqual(session._synthesizer.synthesizer.calls, [])

    def test_kokoro_factory_is_lazy_disabled_and_profile_aware(self):
        with tempfile.TemporaryDirectory() as directory:
            model_dir = Path(directory) / "kokoro"
            model_dir.mkdir()
            for name in ("model.onnx", "voices.bin", "tokens.txt"):
                (model_dir / name).write_bytes(b"model")
            lexicon = Path(directory) / "lexicon.txt"
            lexicon.write_text("test", encoding="utf-8")
            with (
                patch("voice.kokoro_tts.KokoroTTS", FakeLocalTTS),
                patch("voice.desktop_output.importlib.util.find_spec", return_value=object()),
            ):
                session = create_desktop_voice_output({
                    "tts_backend": "kokoro",
                    "tts_rate": 187,
                    "kokoro_model_dir": str(model_dir),
                    "kokoro_lexicon_path": str(lexicon),
                    "kokoro_voice": "bf_emma",
                    "kokoro_speed": 1.1,
                })
        wrapped = session._synthesizer
        self.assertFalse(session.enabled)
        self.assertEqual(wrapped.capabilities.backend, "kokoro")
        self.assertEqual(wrapped.profile.speaking_rate, 187)
        self.assertAlmostEqual(wrapped.synthesizer.speed, 187 / 170.0)
        self.assertFalse(wrapped.enabled)
        self.assertEqual(wrapped.synthesizer.kwargs["enabled"], False)
        self.assertEqual(wrapped.synthesizer.kwargs["voice"], "bf_emma")
        self.assertEqual(wrapped.synthesizer.kwargs["speed"], 1.1)
        self.assertEqual(wrapped.synthesizer.calls, [])

    def test_kokoro_requires_model_directory_and_explicit_lexicon(self):
        with self.assertRaisesRegex(ValueError, "Kokoro model directory"):
            create_desktop_voice_output({"tts_backend": "kokoro"})
        with tempfile.TemporaryDirectory() as directory:
            model_dir = Path(directory)
            with self.assertRaisesRegex(ValueError, "English lexicon"):
                create_desktop_voice_output({
                    "tts_backend": "kokoro",
                    "kokoro_model_dir": str(model_dir),
                })

    def test_factory_rejects_unknown_backend(self):
        with self.assertRaisesRegex(ValueError, "not supported"):
            create_desktop_voice_output({"tts_backend": "remote-service"})

    def test_pyttsx3_requires_optional_package(self):
        with patch("voice.desktop_output.importlib.util.find_spec", return_value=None):
            with self.assertRaisesRegex(ValueError, "pyttsx3"):
                create_desktop_voice_output({"tts_backend": "pyttsx3"})

    def test_factory_defaults_to_piper_but_requires_model(self):
        with self.assertRaisesRegex(ValueError, "existing local Piper"):
            create_desktop_voice_output({})


if __name__ == "__main__":
    unittest.main()
