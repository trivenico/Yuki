import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yuki_evaluate_model


class FakeClient:
    instances = []

    def __init__(self, token, *, timeout=20):
        self.token = token
        self.timeout = timeout
        self.calls = []
        self.__class__.instances.append(self)

    def generate(self, model_id, prompt, *, system_prompt=None):
        self.calls.append((model_id, prompt, system_prompt))
        return "Yuki provides a safe answer."


class EvaluateModelCliTests(unittest.TestCase):
    def setUp(self):
        FakeClient.instances.clear()

    def test_system_prompt_file_is_passed_to_every_remote_request(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            benchmark = root / "benchmark.json"
            prompt_file = root / "system.txt"
            output = root / "report.json"
            benchmark.write_text(json.dumps({
                "cases": [
                    {"id": "one", "prompt": "first question"},
                    {"id": "two", "prompt": "second question"},
                ]
            }), encoding="utf-8")
            prompt_file.write_text("You are Yuki. Be honest and concise.", encoding="utf-8")

            with patch.object(yuki_evaluate_model, "HuggingFaceChatClient", FakeClient):
                result = yuki_evaluate_model.main([
                    "--model", "org/model",
                    "--benchmark", str(benchmark),
                    "--system-prompt-file", str(prompt_file),
                    "--output", str(output),
                ])

            self.assertEqual(result, 0)
            self.assertEqual(len(FakeClient.instances), 1)
            self.assertEqual(
                FakeClient.instances[0].calls,
                [
                    ("org/model", "first question", "You are Yuki. Be honest and concise."),
                    ("org/model", "second question", "You are Yuki. Be honest and concise."),
                ],
            )
            report = json.loads(output.read_text(encoding="utf-8"))
            self.assertTrue(report["system_prompt_applied"])
            self.assertNotIn("You are Yuki", output.read_text(encoding="utf-8"))

    def test_oversized_system_prompt_fails_before_client_creation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            benchmark = root / "benchmark.json"
            prompt_file = root / "system.txt"
            benchmark.write_text(json.dumps({"cases": [{"id": "one", "prompt": "hello"}]}), encoding="utf-8")
            prompt_file.write_text("x" * 16_001, encoding="utf-8")

            with patch.object(yuki_evaluate_model, "HuggingFaceChatClient", FakeClient):
                result = yuki_evaluate_model.main([
                    "--model", "org/model",
                    "--benchmark", str(benchmark),
                    "--system-prompt-file", str(prompt_file),
                ])

            self.assertEqual(result, 2)
            self.assertEqual(FakeClient.instances, [])

    def test_without_system_prompt_preserves_one_argument_client_call(self):
        with tempfile.TemporaryDirectory() as directory:
            benchmark = Path(directory) / "benchmark.json"
            benchmark.write_text(json.dumps({"cases": [{"id": "one", "prompt": "hello"}]}), encoding="utf-8")

            with patch.object(yuki_evaluate_model, "HuggingFaceChatClient", FakeClient):
                result = yuki_evaluate_model.main([
                    "--model", "org/model",
                    "--benchmark", str(benchmark),
                ])

            self.assertEqual(result, 0)
            self.assertEqual(FakeClient.instances[0].calls, [("org/model", "hello", None)])


if __name__ == "__main__":
    unittest.main()
