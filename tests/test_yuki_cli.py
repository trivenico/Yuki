import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from yuki_cli import run_cli


class FakeProvider:
    def __init__(self, available=True, answer="hello"):
        self.available = available
        self.answer = answer
        self.prompts = []

    def is_available(self):
        return self.available

    def generate(self, prompt):
        self.prompts.append(prompt)
        return self.answer

    def generate_chat(self, system, messages):
        self.prompts.append(json.dumps({"system": system, "conversation": messages}))
        return self.answer


class FakeReport:
    def to_dict(self):
        return {"operating_system": "TestOS", "capabilities": {}}


class YukiCliTests(unittest.TestCase):
    def test_ask_uses_configured_provider_and_returns_answer(self):
        provider = FakeProvider(answer="real answer")
        output = []
        code = run_cli(
            ["ask", "hello"],
            config_loader=lambda: {"provider": "fake"},
            provider_factory=lambda config: provider,
            output_fn=output.append,
        )
        self.assertEqual(code, 0)
        self.assertEqual(output, ["real answer"])
        self.assertEqual(provider.prompts, ["hello"])

    def test_provider_status_reports_unavailable_with_nonzero_exit(self):
        provider = FakeProvider(available=False)
        output = []
        code = run_cli(
            ["provider-status"],
            config_loader=lambda: {},
            provider_factory=lambda config: provider,
            output_fn=output.append,
        )
        self.assertEqual(code, 3)
        report = json.loads(output[0])
        self.assertEqual(report["status"], "unavailable")

    def test_doctor_is_json_and_does_not_initialize_provider(self):
        output = []
        def forbidden_provider_factory(config):
            raise AssertionError("doctor must not initialize a provider")
        code = run_cli(
            ["doctor"],
            provider_factory=forbidden_provider_factory,
            readiness_builder=FakeReport,
            output_fn=output.append,
        )
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output[0])["operating_system"], "TestOS")

    def test_ask_returns_nonzero_when_provider_generation_fails(self):
        class FailingProvider(FakeProvider):
            def generate(self, prompt):
                raise RuntimeError("private payload")

        output = []
        code = run_cli(
            ["ask", "hello"],
            config_loader=lambda: {},
            provider_factory=lambda config: FailingProvider(),
            output_fn=output.append,
        )
        self.assertEqual(code, 4)
        self.assertNotIn("private payload", output[0])

    def test_ask_returns_unavailable_exit_code(self):
        output = []
        code = run_cli(
            ["ask", "hello"],
            config_loader=lambda: {},
            provider_factory=lambda config: FakeProvider(available=False),
            output_fn=output.append,
        )
        self.assertEqual(code, 3)
        self.assertIn("unavailable", output[0].lower())

    def test_chat_uses_companion_context_and_reset_command(self):
        provider = FakeProvider(answer="Yuki response")
        output = []
        messages = iter(["first message", "follow-up", "/reset", "fresh start", "/quit"])
        code = run_cli(
            ["chat"],
            config_loader=lambda: {},
            provider_factory=lambda config: provider,
            input_fn=lambda prompt: next(messages),
            output_fn=output.append,
        )
        self.assertEqual(code, 0)
        self.assertEqual(len(provider.prompts), 3)
        self.assertIn("first message", provider.prompts[1])
        self.assertIn("Yuki response", provider.prompts[1])
        self.assertNotIn("first message", provider.prompts[2])
        self.assertTrue(any("conversation has been cleared" in line for line in output))

    def test_chat_handles_provider_failure_without_leaking_exception(self):
        class FlakyProvider(FakeProvider):
            def __init__(self):
                super().__init__()
                self.calls = 0

            def generate(self, prompt):
                self.calls += 1
                if self.calls == 1:
                    raise RuntimeError("secret payload")
                return "recovered"

            def generate_chat(self, system, messages):
                self.calls += 1
                if self.calls == 1:
                    raise RuntimeError("secret payload")
                return "recovered"

        provider = FlakyProvider()
        output = []
        messages = iter(["hello", "retry", "/quit"])
        code = run_cli(
            ["chat"],
            config_loader=lambda: {},
            provider_factory=lambda config: provider,
            input_fn=lambda prompt: next(messages),
            output_fn=output.append,
        )
        self.assertEqual(code, 0)
        self.assertIn("Yuki could not reach the AI provider", output[1])
        self.assertNotIn("secret payload", " ".join(output))
        self.assertIn("Yuki> recovered", output)

    def test_chat_role_command_updates_response_style(self):
        provider = FakeProvider(answer="reviewed")
        output = []
        messages = iter(["/role developer", "review my code", "/quit"])
        code = run_cli(
            ["chat"],
            config_loader=lambda: {},
            provider_factory=lambda config: provider,
            input_fn=lambda prompt: next(messages),
            output_fn=output.append,
        )
        self.assertEqual(code, 0)
        self.assertIn("Interaction style changed to developer", " ".join(output))
        self.assertIn("Current interaction role: developer", provider.prompts[0])
        self.assertIn("Permissions are unchanged", " ".join(output))

    def test_local_auth_cli_bootstrap_login_status_and_logout(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            outputs = []
            root = Path(temp_dir)
            with patch("yuki_cli.data_root", return_value=root):
                bootstrap_passwords = iter(["Correct horse battery staple 9!", "Correct horse battery staple 9!"])
                code = run_cli(["auth", "bootstrap-admin"], input_fn=lambda prompt: "root.user", password_input=lambda prompt: next(bootstrap_passwords), output_fn=outputs.append)
                self.assertEqual(code, 0)
                self.assertTrue((root / ".yuki" / "auth.sqlite3").exists())
                login_passwords = iter(["Correct horse battery staple 9!"])
                code = run_cli(["auth", "login"], input_fn=lambda prompt: "root.user", password_input=lambda prompt: next(login_passwords), output_fn=outputs.append)
                self.assertEqual(code, 0)
                session_path = root / ".yuki" / "session.token"
                self.assertTrue(session_path.exists())
                if os.name == "posix":
                    self.assertEqual(session_path.stat().st_mode & 0o777, 0o600)
                code = run_cli(["auth", "status"], output_fn=outputs.append)
                self.assertEqual(code, 0)
                self.assertIn("root.user", outputs[-1])
                code = run_cli(["auth", "logout"], output_fn=outputs.append)
                self.assertEqual(code, 0)
                self.assertFalse(session_path.exists())
                code = run_cli(["auth", "status"], output_fn=outputs.append)
                self.assertEqual(code, 1)

    def test_auth_cli_rejects_mismatched_bootstrap_passwords(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            outputs = []
            passwords = iter(["first secure password 3!", "second secure password 4!"])
            with patch("yuki_cli.data_root", return_value=Path(temp_dir)):
                code = run_cli(["auth", "bootstrap-admin"], input_fn=lambda prompt: "root.user", password_input=lambda prompt: next(passwords), output_fn=outputs.append)
            self.assertEqual(code, 2)
            self.assertIn("do not match", outputs[-1])
    def test_provider_initialization_failure_is_sanitized(self):
        output = []
        code = run_cli(
            ["ask", "hello"],
            config_loader=lambda: {},
            provider_factory=lambda config: (_ for _ in ()).throw(RuntimeError("secret payload")),
            output_fn=output.append,
        )
        self.assertEqual(code, 2)
        self.assertNotIn("secret payload", output[0])


if __name__ == "__main__":
    unittest.main()
