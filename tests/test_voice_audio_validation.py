import io
import unittest
import wave

from voice.audio_validation import VoiceSampleValidationError, validate_voice_sample


def make_wav(*, channels=1, sample_rate=24000, sample_width=2, seconds=3):
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(channels)
        audio.setsampwidth(sample_width)
        audio.setframerate(sample_rate)
        audio.writeframes(b"\x00" * (channels * sample_width * sample_rate * seconds))
    return output.getvalue()


class VoiceAudioValidationTests(unittest.TestCase):
    def test_accepts_bounded_mono_and_stereo_pcm(self):
        for channels in (1, 2):
            metadata = validate_voice_sample(make_wav(channels=channels), source_owner_consent=True)
            self.assertEqual(metadata.codec, "pcm_s16le")
            self.assertEqual(metadata.channels, channels)
            self.assertEqual(metadata.duration_seconds, 3.0)

    def test_requires_explicit_source_owner_consent(self):
        with self.assertRaises(VoiceSampleValidationError):
            validate_voice_sample(make_wav(), source_owner_consent=False)

    def test_rejects_non_wav_and_malformed_data(self):
        for payload in (b"", b"not audio", b"RIFFxxxxWAVE"):
            with self.assertRaises(VoiceSampleValidationError):
                validate_voice_sample(payload, source_owner_consent=True)

    def test_rejects_unsupported_width_and_sample_rate(self):
        for payload in (make_wav(sample_width=1), make_wav(sample_width=4), make_wav(sample_rate=8000), make_wav(sample_rate=96000)):
            with self.assertRaises(VoiceSampleValidationError):
                validate_voice_sample(payload, source_owner_consent=True)

    def test_rejects_duration_outside_limits(self):
        for seconds in (0, 31):
            with self.assertRaises(VoiceSampleValidationError):
                validate_voice_sample(make_wav(seconds=seconds), source_owner_consent=True)

    def test_rejects_truncated_payload(self):
        with self.assertRaises(VoiceSampleValidationError):
            validate_voice_sample(make_wav()[:-100], source_owner_consent=True)

    def test_rejects_oversized_payload_and_invalid_limit(self):
        payload = make_wav()
        with self.assertRaises(VoiceSampleValidationError):
            validate_voice_sample(payload, source_owner_consent=True, max_bytes=100)
        with self.assertRaises(ValueError):
            validate_voice_sample(payload, source_owner_consent=True, max_bytes=0)

    def test_rejects_declared_frame_count_larger_than_file_before_read(self):
        payload = bytearray(make_wav())
        payload[40:44] = (0xFFFFFFFF).to_bytes(4, "little")
        with self.assertRaises(VoiceSampleValidationError):
            validate_voice_sample(bytes(payload), source_owner_consent=True)

    def test_rejects_too_many_channels(self):
        with self.assertRaises(VoiceSampleValidationError):
            validate_voice_sample(make_wav(channels=3), source_owner_consent=True)


if __name__ == "__main__":
    unittest.main()
