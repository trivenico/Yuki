import unittest
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
    def test_factory_is_disabled_and_does_not_open_audio(self):
        with patch("voice.tts.LocalTTS", FakeLocalTTS):
            session = create_desktop_voice_output({
                "tts_backend": "pyttsx3",
                "tts_rate": 180,
            })
        self.assertFalse(session.enabled)
        self.assertFalse(session._synthesizer.enabled)
        self.assertEqual(session._synthesizer.kwargs["backend"], "pyttsx3")
        self.assertEqual(session._synthesizer.kwargs["rate"], 180)
        self.assertEqual(session._synthesizer.calls, [])

    def test_factory_rejects_unknown_backend(self):
        with self.assertRaisesRegex(ValueError, "not supported"):
            create_desktop_voice_output({"tts_backend": "remote-service"})

    def test_factory_defaults_to_existing_piper_configuration(self):
        with patch("voice.tts.LocalTTS", FakeLocalTTS):
            session = create_desktop_voice_output({})
        self.assertEqual(session._synthesizer.kwargs["backend"], "piper")
        self.assertEqual(session._synthesizer.kwargs["rate"], 170)


if __name__ == "__main__":
    unittest.main()
