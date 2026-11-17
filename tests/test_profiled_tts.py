import unittest

from voice.profile import DEFAULT_VOICE_PROFILE
from voice.profiled_tts import ProfiledLocalSpeechOutput


class FakeSynthesizer:
    def __init__(self, backend):
        self.backend = backend
        self.rate = 170
        self.spoken = []

    def speak(self, text):
        self.spoken.append(text)


class ProfiledTTSTests(unittest.TestCase):
    def test_pyttsx3_applies_only_supported_speaking_rate(self):
        synth = FakeSynthesizer("pyttsx3")
        output = ProfiledLocalSpeechOutput(synth, DEFAULT_VOICE_PROFILE)
        self.assertEqual(synth.rate, DEFAULT_VOICE_PROFILE.speaking_rate)
        self.assertEqual(output.capabilities.supported_controls, frozenset({"speaking_rate"}))
        self.assertEqual(
            output.capabilities.unsupported_controls,
            frozenset({"pitch", "expressiveness", "style"}),
        )
        self.assertEqual(output.capability_report()["requested_style"], DEFAULT_VOICE_PROFILE.style)

    def test_piper_does_not_claim_unsupported_rate_or_anime_style(self):
        synth = FakeSynthesizer("piper")
        output = ProfiledLocalSpeechOutput(synth, DEFAULT_VOICE_PROFILE)
        self.assertEqual(synth.rate, 170)
        self.assertEqual(output.capabilities.supported_controls, frozenset())
        self.assertEqual(
            output.capabilities.unsupported_controls,
            frozenset({"speaking_rate", "pitch", "expressiveness", "style"}),
        )

    def test_construction_never_starts_playback(self):
        synth = FakeSynthesizer("pyttsx3")
        ProfiledLocalSpeechOutput(synth, DEFAULT_VOICE_PROFILE)
        self.assertEqual(synth.spoken, [])

    def test_explicit_speak_delegates_to_backend(self):
        synth = FakeSynthesizer("pyttsx3")
        ProfiledLocalSpeechOutput(synth, DEFAULT_VOICE_PROFILE).speak("Hello.")
        self.assertEqual(synth.spoken, ["Hello."])

    def test_stream_fallback_skips_empty_chunks_and_enforces_chunk_bound(self):
        synth = FakeSynthesizer("unknown")
        output = ProfiledLocalSpeechOutput(synth, DEFAULT_VOICE_PROFILE)
        self.assertEqual(
            output.speak_stream(["Hello", "", "Yuki"]),
            {"chunks_spoken": 2, "interrupted": False},
        )
        self.assertEqual(synth.spoken, ["Hello", "Yuki"])
        with self.assertRaisesRegex(ValueError, "2000 characters"):
            output.speak_stream(["x" * 2001])
        with self.assertRaisesRegex(ValueError, "must be text"):
            output.speak_stream(["ok", object()])

    def test_stream_fallback_enforces_total_chunk_limit(self):
        synth = FakeSynthesizer("unknown")
        output = ProfiledLocalSpeechOutput(synth, DEFAULT_VOICE_PROFILE)
        with self.assertRaisesRegex(ValueError, "100-chunk"):
            output.speak_stream(["x"] * 101)
        self.assertEqual(len(synth.spoken), 100)

    def test_stream_fallback_enforces_total_character_limit(self):
        synth = FakeSynthesizer("unknown")
        output = ProfiledLocalSpeechOutput(synth, DEFAULT_VOICE_PROFILE)
        with self.assertRaisesRegex(ValueError, "20000-character"):
            output.speak_stream(["x" * 2000] * 11)
        self.assertEqual(sum(map(len, synth.spoken)), 20000)

    def test_invalid_backend_object_is_rejected(self):
        with self.assertRaises(TypeError):
            ProfiledLocalSpeechOutput(object(), DEFAULT_VOICE_PROFILE)


if __name__ == "__main__":
    unittest.main()
