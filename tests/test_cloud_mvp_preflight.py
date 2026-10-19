import unittest
from scripts.cloud_mvp_preflight import evaluate

class CloudMvpPreflightTests(unittest.TestCase):
    def test_mock_mode_never_requires_credentials(self):
        errors, warnings, facts = evaluate({"provider": "mock"}, {}, None)
        self.assertEqual(errors, [])
        self.assertFalse(facts["cloud_key_present"])
        self.assertTrue(any("Mock provider" in item for item in warnings))

    def test_hybrid_requires_local_model_and_reachability(self):
        errors, _, _ = evaluate({"provider": "hybrid", "ollama_model": ""}, {}, False)
        self.assertTrue(any("YUKI_OLLAMA_MODEL" in item for item in errors))
        errors, _, _ = evaluate({"provider": "hybrid", "ollama_model": "small-local"}, {}, False)
        self.assertTrue(any("not reachable" in item for item in errors))

    def test_hybrid_cloud_route_requires_model_and_key(self):
        config = {"provider": "hybrid", "ollama_model": "small-local"}
        env = {"YUKI_CLOUD_TASKS": "research", "YUKI_CLOUD_MODEL": "example-model"}
        errors, _, facts = evaluate(config, env, True)
        self.assertTrue(any("API-key environment variable is empty" in item for item in errors))
        self.assertFalse(facts["cloud_key_present"])

    def test_hybrid_with_local_and_explicit_cloud_credentials_is_ready(self):
        config = {"provider": "hybrid", "ollama_model": "small-local"}
        env = {"YUKI_CLOUD_TASKS": "research,writing", "YUKI_CLOUD_MODEL": "example-model",
               "OPENAI_API_KEY": "test-not-a-real-secret"}
        errors, _, facts = evaluate(config, env, True)
        self.assertEqual(errors, [])
        self.assertTrue(facts["cloud_key_present"])

    def test_invalid_provider_does_not_disclose_env(self):
        errors, _, _ = evaluate({"provider": "surprise"}, {"OPENAI_API_KEY": "never-print-this"}, None)
        self.assertTrue(errors)
        self.assertNotIn("never-print-this", str(errors))

    def test_cloud_tasks_without_model_are_rejected(self):
        config = {"provider": "hybrid", "ollama_model": "small-local"}
        errors, _, _ = evaluate(config, {"YUKI_CLOUD_TASKS": "research"}, True)
        self.assertTrue(any("YUKI_CLOUD_MODEL" in item for item in errors))

if __name__ == "__main__": unittest.main()