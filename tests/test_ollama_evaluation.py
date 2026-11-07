import unittest
from unittest.mock import Mock

from providers.ollama import OllamaProvider
from providers.ollama_evaluation import OllamaEvaluationAdapter
from providers.unified_model_evaluation import evaluate_candidate


class OllamaEvaluationAdapterTests(unittest.TestCase):
    def make_provider(self):
        provider = OllamaProvider(model="yuki-core:0.1")
        provider.generate_chat = Mock(return_value={
            "text": "alpha beta",
            "tool_calls": {"tool_calls": []},
            "finish_reason": "stop",
        })
        return provider

    def test_adapter_runs_local_provider_without_external_credentials(self):
        provider = self.make_provider()
        adapter = OllamaEvaluationAdapter(provider, artifact_sha256="a" * 64)
        report = evaluate_candidate(
            model_id=adapter.model_id,
            provider_id=adapter.provider_id,
            runtime_id=adapter.runtime_id,
            benchmark_id="yuki-smoke",
            benchmark_version="1.0.0",
            cases=[
                {"id": "case-1", "prompt": "Say alpha", "expected_terms": ["alpha"]},
                {"id": "case-2", "prompt": "Say beta", "expected_terms": ["beta"]},
            ],
            generate=adapter,
            artifact_sha256=adapter.artifact_sha256,
        )
        self.assertEqual(report["provider_id"], "ollama")
        self.assertEqual(report["successful_cases"], 2)
        self.assertEqual(report["mean_expected_term_coverage"], 1.0)
        self.assertEqual(provider.generate_chat.call_count, 2)
        self.assertEqual(provider.generate_chat.call_args.kwargs["tool_choice"], "none")

    def test_rejects_tool_proposals_without_executing_them(self):
        provider = self.make_provider()
        provider.generate_chat.return_value = {
            "text": "answer",
            "tool_calls": {"tool_calls": [{"name": "dangerous"}]},
        }
        adapter = OllamaEvaluationAdapter(provider)
        with self.assertRaisesRegex(RuntimeError, "tool_calls_not_permitted"):
            adapter("test prompt")
        self.assertEqual(provider.generate_chat.call_count, 1)

    def test_rejects_invalid_provider_and_digest(self):
        with self.assertRaisesRegex(TypeError, "OllamaProvider"):
            OllamaEvaluationAdapter(object())
        with self.assertRaisesRegex(ValueError, "artifact_sha256"):
            OllamaEvaluationAdapter(self.make_provider(), artifact_sha256="latest")

    def test_rejects_empty_prompt(self):
        adapter = OllamaEvaluationAdapter(self.make_provider())
        with self.assertRaisesRegex(ValueError, "prompt_must_be_non_empty"):
            adapter("  ")


if __name__ == "__main__":
    unittest.main()
