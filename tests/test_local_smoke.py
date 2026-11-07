"""Tests for the deterministic local specialist-runtime smoke check."""
import tempfile
import unittest

from scripts.local_smoke import run_smoke


class LocalSmokeTests(unittest.TestCase):
    def test_fake_provider_smoke_completes_with_durable_budget(self):
        with tempfile.TemporaryDirectory() as directory:
            result = run_smoke(directory)
        self.assertEqual(result["result"], "passed")
        self.assertEqual(result["mode"], "fake-provider-wiring-only")
        self.assertEqual(result["task_state"], "succeeded")
        self.assertEqual(result["provider_calls"], 1)
        self.assertEqual(result["budget_usage"]["model_turns"], 1)
        self.assertGreater(result["budget_usage"]["transcript_chars"], 0)


if __name__ == "__main__":
    unittest.main()
