import unittest

from yuki_identity import (
    PRODUCT_NAME,
    DEFAULT_WAKE_WORD,
    DEFAULT_WAKE_PHRASE,
    DEFAULT_GREETING,
    VOICE_STYLE,
    VOICE_ACTIVATION_DEFAULT_ENABLED,
)


class YukiIdentityTests(unittest.TestCase):
    def test_yuki_is_the_only_default_wake_word(self):
        self.assertEqual(PRODUCT_NAME, "Yuki")
        self.assertEqual(DEFAULT_WAKE_WORD, "Yuki")
        self.assertEqual(DEFAULT_WAKE_PHRASE, "Yuki")
        self.assertNotIn("Hey", DEFAULT_WAKE_PHRASE)

    def test_companion_greeting_and_voice_style_are_configured(self):
        self.assertIn("Master", DEFAULT_GREETING)
        self.assertIn("help you", DEFAULT_GREETING)
        self.assertIn("Japanese female", VOICE_STYLE)
        self.assertFalse(VOICE_ACTIVATION_DEFAULT_ENABLED)


if __name__ == "__main__":
    unittest.main()
