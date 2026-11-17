import unittest

from voice.language import SUPPORTED_VOICE_STYLES, voice_style_instruction
from voice.profile import DEFAULT_VOICE_PROFILE, VoiceProfile


class VoiceProfileTests(unittest.TestCase):
    def test_default_profile_is_feminine_anime_inspired_english(self):
        profile = DEFAULT_VOICE_PROFILE
        self.assertEqual(profile.language, "en")
        self.assertEqual(profile.style, "japanese_anime_female_english")
        self.assertIn(profile.style, SUPPORTED_VOICE_STYLES)
        self.assertIn("Speak in English", profile.instruction())
        self.assertIn("Japanese-anime-inspired", profile.instruction())

    def test_profile_controls_are_bounded(self):
        with self.assertRaises(ValueError):
            VoiceProfile(speaking_rate=500)
        with self.assertRaises(ValueError):
            VoiceProfile(pitch=1.5)
        with self.assertRaises(ValueError):
            VoiceProfile(expressiveness=-0.1)
        with self.assertRaises(ValueError):
            VoiceProfile(language="ja")

    def test_style_does_not_request_specific_person_or_character_imitation(self):
        instruction = voice_style_instruction("japanese_anime_female_english")
        self.assertIn("Do not imitate a specific", instruction)

    def test_unknown_style_is_rejected(self):
        with self.assertRaises(ValueError):
            voice_style_instruction("celebrity_clone")


if __name__ == "__main__":
    unittest.main()
