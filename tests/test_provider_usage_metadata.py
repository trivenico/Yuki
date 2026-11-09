import unittest

from providers.ollama import _normalize_ollama_usage
from providers.openai_compatible import _normalize_openai_usage


class ProviderUsageMetadataTests(unittest.TestCase):
    def test_ollama_exact_usage(self):
        self.assertEqual(
            _normalize_ollama_usage({"prompt_eval_count": 21, "eval_count": 13}),
            {"input_tokens": 21, "output_tokens": 13, "estimated": False},
        )

    def test_ollama_missing_or_invalid_usage_is_unknown(self):
        for payload in (
            {}, {"prompt_eval_count": 1}, {"prompt_eval_count": -1, "eval_count": 2},
            {"prompt_eval_count": True, "eval_count": 2}, None,
        ):
            with self.subTest(payload=payload):
                self.assertIsNone(_normalize_ollama_usage(payload))

    def test_openai_compatible_exact_usage(self):
        self.assertEqual(
            _normalize_openai_usage({"prompt_tokens": 31, "completion_tokens": 7}),
            {"input_tokens": 31, "output_tokens": 7, "estimated": False},
        )

    def test_openai_missing_or_invalid_usage_is_unknown(self):
        for payload in (
            {}, {"prompt_tokens": 1}, {"prompt_tokens": 1, "completion_tokens": -2},
            {"prompt_tokens": True, "completion_tokens": 2}, None,
        ):
            with self.subTest(payload=payload):
                self.assertIsNone(_normalize_openai_usage(payload))


if __name__ == "__main__":
    unittest.main()
