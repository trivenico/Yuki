import unittest

from voice.language import (
    normalize_language_code,
    same_language_reply_instruction,
    should_speak_reply,
)


class MultilingualVoicePolicyTests(unittest.TestCase):
    def test_normalizes_language_tags_conservatively(self):
        self.assertEqual(normalize_language_code(" ja "), "ja")
        self.assertEqual(normalize_language_code("zh-hant-tw"), "zh-Hant-TW")
        self.assertIsNone(normalize_language_code("not a language"))

    def test_reply_instruction_uses_detected_language(self):
        instruction = same_language_reply_instruction("ne")
        self.assertIn("'ne'", instruction)
        self.assertIn("Reply in that language", instruction)
        self.assertIsNone(same_language_reply_instruction(None))

    def test_speech_requires_enabled_voice_mode_and_nonempty_reply(self):
        self.assertTrue(should_speak_reply(
            voice_enabled=True, voice_mode_active=True, reply_text="こんにちは"
        ))
        self.assertFalse(should_speak_reply(
            voice_enabled=False, voice_mode_active=True, reply_text="hello"
        ))
        self.assertFalse(should_speak_reply(
            voice_enabled=True, voice_mode_active=False, reply_text="hello"
        ))
        self.assertFalse(should_speak_reply(
            voice_enabled=True, voice_mode_active=True, reply_text=" "
        ))


if __name__ == "__main__":
    unittest.main()
