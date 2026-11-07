"""Safe opt-in behavior tests for the real-provider acceptance command."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "real_provider_acceptance.py"
SPEC = importlib.util.spec_from_file_location("yuki_real_provider_acceptance", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class _Provider:
    model = "deterministic-test-model"

    def __init__(self, response="Yuki local provider acceptance passed.", available=True):
        self.response = response
        self.available = available
        self.calls = 0

    def is_available(self):
        return self.available

    def generate(self, prompt):
        self.calls += 1
        self.prompt = prompt
        return self.response


class RealProviderAcceptanceTests(unittest.TestCase):
    def test_skips_without_explicit_opt_in_and_never_constructs_provider(self):
        calls = []
        result = MODULE.run_acceptance(
            environ={},
            config_loader=lambda: {},
            provider_factory=lambda config: calls.append(config),
        )
        self.assertEqual(result["result"], "skipped")
        self.assertEqual(calls, [])

    def test_sends_exactly_one_fixed_prompt_after_opt_in(self):
        provider = _Provider()
        result = MODULE.run_acceptance(
            environ={"YUKI_RUN_REAL_PROVIDER_ACCEPTANCE": "1"},
            config_loader=lambda: {"provider": "ollama"},
            provider_factory=lambda config: provider,
        )
        self.assertEqual(result["result"], "passed")
        self.assertEqual(provider.calls, 1)
        self.assertEqual(
            provider.prompt,
            "Reply with exactly: Yuki local provider acceptance passed.",
        )
        self.assertNotIn("response", result)

    def test_unavailable_provider_is_not_called(self):
        provider = _Provider(available=False)
        with self.assertRaisesRegex(RuntimeError, "not available"):
            MODULE.run_acceptance(
                environ={"YUKI_RUN_REAL_PROVIDER_ACCEPTANCE": "1"},
                config_loader=lambda: {"provider": "ollama"},
                provider_factory=lambda config: provider,
            )
        self.assertEqual(provider.calls, 0)

    def test_empty_and_oversized_responses_fail(self):
        for response in (" ", "x" * (MODULE.MAX_RESPONSE_CHARS + 1)):
            with self.subTest(response_length=len(response)):
                provider = _Provider(response=response)
                with self.assertRaises(RuntimeError):
                    MODULE.run_acceptance(
                        environ={"YUKI_RUN_REAL_PROVIDER_ACCEPTANCE": "1"},
                        config_loader=lambda: {"provider": "ollama"},
                        provider_factory=lambda config: provider,
                    )
                self.assertEqual(provider.calls, 1)

    def test_unsupported_provider_fails_before_provider_construction(self):
        calls = []
        with self.assertRaisesRegex(RuntimeError, "not supported"):
            MODULE.run_acceptance(
                environ={"YUKI_RUN_REAL_PROVIDER_ACCEPTANCE": "1"},
                config_loader=lambda: {"provider": "mock"},
                provider_factory=lambda config: calls.append(config),
            )
        self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()
