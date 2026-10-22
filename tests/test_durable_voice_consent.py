"""Regression tests for durable approval around microphone and speech operations."""
import ast
import unittest
from pathlib import Path


class DurableVoiceConsentTests(unittest.TestCase):
    def setUp(self):
        source = (Path(__file__).resolve().parents[1] / "main.py").read_text(encoding="utf-8")
        self.tree = ast.parse(source)
        self.main = next(
            node for node in self.tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "main"
        )

    def test_voice_operations_are_allowlisted(self):
        registered = set()
        for node in ast.walk(self.main):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
                continue
            if node.func.id != "DurableOperationRuntime":
                continue
            for keyword in node.keywords:
                if keyword.arg == "operations" and isinstance(keyword.value, ast.Dict):
                    registered.update(
                        key.value for key in keyword.value.keys
                        if isinstance(key, ast.Constant) and isinstance(key.value, str)
                    )
        self.assertTrue({
            "voice_record", "voice_transcribe", "voice_speak", "voice_wakeword_listen"
        }.issubset(registered))

    def test_microphone_and_speech_commands_use_durable_runtime(self):
        expected = {
            "/voice record ": "voice_record",
            "/voice transcribe ": "voice_transcribe",
            "/voice speak ": "voice_speak",
            "/voice wakeword listen ": "voice_wakeword_listen",
        }
        found = {}
        for node in ast.walk(self.main):
            if not isinstance(node, ast.If):
                continue
            test = node.test
            literal = None
            if (
                isinstance(test, ast.Call) and isinstance(test.func, ast.Attribute)
                and test.func.attr == "startswith" and isinstance(test.func.value, ast.Name)
                and test.func.value.id == "command" and test.args
                and isinstance(test.args[0], ast.Constant) and isinstance(test.args[0].value, str)
            ):
                literal = test.args[0].value
            if literal in expected:
                found[literal] = node.body
        self.assertEqual(set(found), set(expected))
        for command, body in found.items():
            calls = [
                item for statement in body for item in ast.walk(statement)
                if isinstance(item, ast.Call) and isinstance(item.func, ast.Attribute)
                and item.func.attr == "execute"
                and isinstance(item.func.value, ast.Name)
                and item.func.value.id == "durable_operations"
            ]
            self.assertTrue(calls, f"{command} bypasses durable consent/audit/ledger")
            self.assertTrue(
                any(
                    isinstance(arg, ast.Constant) and arg.value == expected[command]
                    for call in calls for arg in call.args
                ),
                f"{command} uses an unexpected operation",
            )

    def test_wakeword_capture_is_not_background_listening(self):
        source = (Path(__file__).resolve().parents[1] / "main.py").read_text(encoding="utf-8")
        self.assertIn("wakeword.listen_once(seconds=seconds)", source)
        self.assertIn("continuous listening is not implemented", source)


if __name__ == "__main__":
    unittest.main()
