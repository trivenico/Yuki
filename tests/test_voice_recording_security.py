import os
import sys
import tempfile
import types
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

from voice.stt import FasterWhisperSTT, STTError


class VoiceRecordingSecurityTests(unittest.TestCase):
    def setUp(self):
        self.stt = FasterWhisperSTT(enabled=True)

    def _mock_audio_modules(self, *, fail=False):
        sounddevice = types.ModuleType("sounddevice")
        sounddevice.rec = lambda *args, **kwargs: [1, 2, 3, 4]
        sounddevice.wait = lambda: None
        numpy = types.ModuleType("numpy")
        numpy.int16 = int
        numpy.asarray = lambda values, dtype=None: types.SimpleNamespace(
            tobytes=lambda: b"\\x01\\x00" * len(values)
        )
        if fail:
            sounddevice.rec = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("device error"))
        return {"sounddevice": sounddevice, "numpy": numpy}

    def test_recording_is_created_with_private_permissions_and_valid_wav(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "recording.wav"
            with patch.dict(sys.modules, self._mock_audio_modules()):
                result = self.stt.record_wav(output, seconds=1)
            self.assertEqual(result, output)
            with wave.open(str(output), "rb") as audio:
                self.assertEqual(audio.getnchannels(), 1)
                self.assertEqual(audio.getframerate(), 16000)
                self.assertEqual(audio.getnframes(), 4)
            if os.name == "posix":
                self.assertEqual(output.stat().st_mode & 0o777, 0o600)

    def test_existing_file_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "recording.wav"
            output.write_bytes(b"original")
            with patch.dict(sys.modules, self._mock_audio_modules()):
                with self.assertRaises(STTError):
                    self.stt.record_wav(output)
            self.assertEqual(output.read_bytes(), b"original")

    def test_existing_symlink_is_not_followed(self):
        if not hasattr(os, "symlink"):
            self.skipTest("symlinks are unavailable")
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "target.wav"
            target.write_bytes(b"protected")
            output = Path(directory) / "recording.wav"
            try:
                output.symlink_to(target)
            except OSError:
                self.skipTest("symlink creation is not permitted")
            with patch.dict(sys.modules, self._mock_audio_modules()):
                with self.assertRaises(STTError):
                    self.stt.record_wav(output)
            self.assertEqual(target.read_bytes(), b"protected")

    def test_failed_recording_removes_partial_output(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "recording.wav"
            with patch.dict(sys.modules, self._mock_audio_modules(fail=True)):
                with self.assertRaises(STTError):
                    self.stt.record_wav(output)
            self.assertFalse(output.exists())

    def test_invalid_sample_rate_is_rejected(self):
        for sample_rate in (True, 0, 48000, "16000"):
            with self.subTest(sample_rate=sample_rate):
                with self.assertRaises(ValueError):
                    self.stt.record_wav("unused.wav", sample_rate=sample_rate)


if __name__ == "__main__":
    unittest.main()
