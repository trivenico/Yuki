import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from voice.desktop_output import create_desktop_voice_output


class FakeLocalTTS:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.enabled = kwargs["enabled"]
        self.calls = []

    def speak_stream(self, chunks):
        self.calls.append(list(chunks))

    def stop(self):
        pass


class DesktopVoiceOutputFactoryTests(unittest.TestCase):
    def test_piper_requires_existing_model_and_runtime(self):
        with self.assertRaisesRegex(ValueError, "existing local Piper"):
            create_desktop_voice_output({"tts_backend": "piper"})

    def test_factory_is_disabled_and_does_not_open_audio(self):
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
        self.assertFalse(session._synthesizer.enabled)
        self.assertEqual(session._synthesizer.kwargs["backend"], "piper")
        self.assertEqual(session._synthesizer.kwargs["rate"], 180)
        self.assertEqual(session._synthesizer.calls, [])

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
