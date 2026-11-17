import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from voice.tts import LocalTTS, TTSError


class RecordingTTS(LocalTTS):
    def __init__(self):
        super().__init__(enabled=True, backend="pyttsx3")
        self.spoken = []

    def speak(self, text):
        self.spoken.append(text)


class LocalTTSBoundsTests(unittest.TestCase):
    def test_stream_accepts_bounded_chunks(self):
        tts = RecordingTTS()
        result = tts.speak_stream(["hello", " ", "world"])
        self.assertEqual(tts.spoken, ["hello", "world"])
        self.assertEqual(result, {"chunks_spoken": 2, "interrupted": False})

    def test_stream_rejects_more_than_100_chunks(self):
        tts = RecordingTTS()
        with self.assertRaisesRegex(ValueError, "100-chunk"):
            tts.speak_stream(["x"] * 101)
        self.assertEqual(len(tts.spoken), 100)

    def test_stream_rejects_more_than_20000_characters(self):
        tts = RecordingTTS()
        with self.assertRaisesRegex(ValueError, "20000-character"):
            tts.speak_stream(["x" * 2000] * 11)

    def test_stream_rejects_non_text_and_oversized_chunks(self):
        tts = RecordingTTS()
        with self.assertRaisesRegex(ValueError, "text"):
            tts.speak_stream(["ok", object()])
        with self.assertRaisesRegex(ValueError, "2000 characters"):
            tts.speak_stream(["x" * 2001])

    def test_stop_interrupts_stream_before_next_chunk(self):
        tts = RecordingTTS()

        def chunks():
            yield "first"
            tts.stop()
            yield "second"

        result = tts.speak_stream(chunks())
        self.assertEqual(tts.spoken, ["first"])
        self.assertTrue(result["interrupted"])

    def test_disabled_output_never_speaks(self):
        tts = RecordingTTS()
        tts.enabled = False
        with self.assertRaises(TTSError):
            tts.speak_stream(["hello"])
        self.assertEqual(tts.spoken, [])

    def test_audio_duration_is_rejected_before_loading_audio(self):
        class Info:
            samplerate = 24_000
            channels = 1
            frames = 24_000 * 121
            duration = 121

        fake_sf = types.SimpleNamespace(info=Mock(return_value=Info()), read=Mock())
        fake_sd = types.SimpleNamespace(play=Mock(), stop=Mock(), get_stream=Mock())
        with tempfile.TemporaryDirectory() as directory:
            model = Path(directory) / "voice.onnx"
            model.write_bytes(b"model")
            original_run = __import__("subprocess").run

            def create_audio(*args, **kwargs):
                output = Path(kwargs["args"][kwargs["args"].index("--output_file") + 1]) if "args" in kwargs else Path(args[0][args[0].index("--output_file") + 1])
                output.write_bytes(b"RIFF" + b"x" * 64)
                return Mock(returncode=0)

            tts = LocalTTS(enabled=True, model_path=model)
            with patch.dict(sys.modules, {"sounddevice": fake_sd, "soundfile": fake_sf}), \
                 patch("voice.tts.subprocess.run", side_effect=create_audio):
                with self.assertRaisesRegex(TTSError, "120-second"):
                    tts.speak("hello")
            fake_sf.read.assert_not_called()
            fake_sd.play.assert_not_called()


if __name__ == "__main__":
    unittest.main()
