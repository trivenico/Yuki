import tempfile
import threading
import time
import unittest
from pathlib import Path

from voice.session import VoiceSession, VoiceSessionError


class FakeSTT:
    def __init__(self, *, block=False):
        self.enabled = True
        self.block = block
        self.audio_path = None
        self.transcribed_path = None
        self.started = threading.Event()

    def record_wav(self, path, *, seconds, sample_rate, cancellation_event):
        self.audio_path = Path(path)
        self.audio_path.write_bytes(b"temporary audio")
        self.started.set()
        if self.block:
            while not cancellation_event.wait(0.01):
                pass
            raise RuntimeError("capture cancelled")

    def transcribe_file(self, path, *, language=None):
        self.transcribed_path = Path(path)
        self.assert_exists_during_transcription = self.transcribed_path.is_file()
        return {"text": "  Hello Yuki.  ", "language": "en"}


class VoiceSessionTests(unittest.TestCase):
    def test_construction_does_not_open_microphone_and_requires_opt_in(self):
        stt = FakeSTT()
        session = VoiceSession(enabled=False, stt=stt)
        self.assertFalse(session.enabled)
        with self.assertRaises(VoiceSessionError):
            session.capture_and_transcribe()
        self.assertIsNone(stt.audio_path)

    def test_audio_is_ephemeral_and_transcript_is_trimmed(self):
        stt = FakeSTT()
        session = VoiceSession(enabled=True, stt=stt)
        transcript = session.capture_and_transcribe(seconds=3)
        self.assertEqual(transcript, "Hello Yuki.")
        self.assertTrue(stt.assert_exists_during_transcription)
        self.assertFalse(stt.audio_path.exists())
        self.assertFalse(stt.transcribed_path.exists())

    def test_disable_cancels_active_capture(self):
        stt = FakeSTT(block=True)
        session = VoiceSession(enabled=True, stt=stt)
        errors = []

        def capture():
            try:
                session.capture_and_transcribe()
            except Exception as exc:
                errors.append(exc)

        worker = threading.Thread(target=capture)
        worker.start()
        self.assertTrue(stt.started.wait(2))
        session.disable()
        worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertFalse(session.enabled)
        self.assertTrue(errors)
        self.assertFalse(stt.audio_path.exists())

    def test_close_prevents_reactivation(self):
        session = VoiceSession(enabled=True, stt=FakeSTT())
        session.close()
        with self.assertRaises(VoiceSessionError):
            session.enable()
        with self.assertRaises(VoiceSessionError):
            session.capture_and_transcribe()

    def test_capture_duration_is_bounded(self):
        session = VoiceSession(enabled=True, stt=FakeSTT())
        for seconds in (True, 0, 11, "5"):
            with self.subTest(seconds=seconds):
                with self.assertRaises(ValueError):
                    session.capture_and_transcribe(seconds=seconds)


if __name__ == "__main__":
    unittest.main()
