import unittest

from yuki_training.byte_tokenizer import ByteTokenizer


class ByteTokenizerTests(unittest.TestCase):
    def test_round_trips_ascii_and_unicode(self):
        tokenizer = ByteTokenizer()
        text = "Yuki — JARVIS 🤖"
        self.assertEqual(tokenizer.decode(tokenizer.encode(text)), text)

    def test_vocab_is_exactly_utf8_byte_range(self):
        tokenizer = ByteTokenizer()
        self.assertEqual(tokenizer.vocab_size, 256)
        self.assertEqual(tokenizer.encode("\x00\xff"), [0, 195, 191])

    def test_rejects_invalid_token_ids(self):
        with self.assertRaises(ValueError):
            ByteTokenizer().decode([256])

    def test_rejects_non_string_input(self):
        with self.assertRaises(TypeError):
            ByteTokenizer().encode(42)


if __name__ == "__main__":
    unittest.main()
