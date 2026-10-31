import json
import unittest
from unittest.mock import patch

from yuki_cli import run_cli
from main import _one_path_argument


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


class FakeReport:
    def to_dict(self):
        return {"operating_system": "TestOS", "capabilities": {}}


class YukiCliTests(unittest.TestCase):
    def test_windows_manifest_paths_preserve_backslashes_and_spaces(self):
        self.assertEqual(
            _one_path_argument(r'"C:\Users\Test User\skill.json"'),
            r'C:\Users\Test User\skill.json',
        )

    def test_skills_list_command_returns_json_without_provider_initialization(self):
        output = []
        with patch("yuki_cli.list_extensions", return_value=[{"id": "writing_helper", "name": "writing_helper", "valid": True}]):
            code = run_cli(["skills", "list"], output_fn=output.append)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output[0])["installed"][0]["id"], "writing_helper")

    def test_skills_lint_returns_structured_advisory_report(self):
        output = []
        with patch("core.skill_linter.lint_skill", return_value=[]) as lint:
            code = run_cli(["skills", "lint", "/tmp/SKILL.md"], output_fn=output.append)
        self.assertEqual(code, 0)
        lint.assert_called_once()
        report = json.loads(output[0])
        self.assertTrue(report["valid"])
        self.assertEqual(report["warnings"], 0)

    def test_skills_lint_returns_nonzero_when_hard_lint_errors_are_found(self):
        from core.skill_linter import LintFinding
        output = []
        with patch("core.skill_linter.lint_skill", return_value=[
            LintFinding("error", "name-format", "invalid name")
        ]):
            code = run_cli(["skills", "lint", "/tmp/SKILL.md"], output_fn=output.append)
        self.assertEqual(code, 1)
        self.assertFalse(json.loads(output[0])["valid"])

    def test_skills_add_previews_repository_and_requires_approval(self):
        output = []
        preview = {
            "repository": "Leonxlnx/taste-skill",
            "commit": "a" * 40,
            "skill_document": "skills/taste-skill/SKILL.md",
            "files_in_skill_directory": ["SKILL.md", "scripts/draw.mjs"],
            "executable_script_candidates": ["scripts/draw.mjs"],
        }
        with patch(
            "yuki_cli.install_github_skill",
            return_value={"installed": False, "cancelled": True, "preview": preview},
        ) as installer:
            code = run_cli(
                ["skills", "add", "Leonxlnx/taste-skill"],
                input_fn=lambda _: "n",
                output_fn=output.append,
            )
        self.assertEqual(code, 1)
        installer.assert_called_once()
        self.assertEqual(installer.call_args.args[0], "Leonxlnx/taste-skill")
        self.assertFalse(installer.call_args.kwargs["approve"](preview))
        self.assertTrue(any("Supporting files and scripts" in line for line in output))

    def test_skills_add_supports_explicit_path_and_approval_flag(self):
        output = []
        with patch(
            "yuki_cli.install_github_skill",
            return_value={"installed": True, "id": "design_taste_frontend", "source_commit": "a" * 40},
        ) as installer:
            code = run_cli(
                ["skills", "add", "Leonxlnx/taste-skill", "--path", "skills/taste-skill", "--yes"],
                output_fn=output.append,
            )
        self.assertEqual(code, 0)
        self.assertEqual(installer.call_args.kwargs["skill_path"], "skills/taste-skill")
        self.assertTrue(installer.call_args.kwargs["approve"]({}))

    def test_skills_import_requires_confirmation_and_reports_lint_preview(self):
        output = []
        report = {"valid": True, "errors": 0, "warnings": 1, "findings": []}
        with patch("core.skill_linter.lint_skill", return_value=[]), patch(
            "core.skill_linter.findings_as_json", return_value=report
        ), patch(
            "yuki_cli.install_skill_file",
            return_value={"id": "release_notes", "name": "release_notes", "path": "/tmp/release_notes.json"},
        ) as importer:
            code = run_cli(
                ["skills", "import", "/tmp/release-notes"],
                input_fn=lambda _: "yes",
                output_fn=output.append,
            )
        self.assertEqual(code, 0)
        importer.assert_called_once_with("/tmp/release-notes")
        self.assertTrue(json.loads(output[0])["review_required"])
        result = json.loads(output[-1])
        self.assertEqual(result["installed"]["id"], "release_notes")
        self.assertIn("not installed or executed", result["note"])

    def test_skills_import_directory_lints_its_skill_document(self):
        import tempfile
        from pathlib import Path

        output = []
        with tempfile.TemporaryDirectory() as temporary_root:
            skill_dir = Path(temporary_root) / "directory-skill"
            skill_dir.mkdir()
            (skill_dir / "SKILL.md").write_text(
                "---\\nname: directory-skill\\ndescription: Test skill import.\\n---\\n",
                encoding="utf-8",
            )
            with patch("core.skill_linter.lint_skill", return_value=[]) as lint, patch(
                "core.skill_linter.findings_as_json",
                return_value={"valid": True, "errors": 0, "warnings": 0, "findings": []},
            ), patch(
                "yuki_cli.install_skill_file",
                return_value={"id": "directory_skill", "name": "directory_skill", "path": "/tmp/directory_skill.json"},
            ):
                code = run_cli(
                    ["skills", "import", str(skill_dir)],
                    input_fn=lambda _: "yes",
                    output_fn=output.append,
                )
            self.assertEqual(code, 0)
            lint.assert_called_once_with(skill_dir / "SKILL.md")

    def test_skills_import_defaults_to_cancel_when_not_confirmed(self):
        output = []
        with patch("core.skill_linter.lint_skill", return_value=[]), patch(
            "core.skill_linter.findings_as_json",
            return_value={"valid": True, "errors": 0, "warnings": 0, "findings": []},
        ), patch("yuki_cli.install_skill_file") as importer:
            code = run_cli(
                ["skills", "import", "/tmp/release-notes"],
                input_fn=lambda _: "n",
                output_fn=output.append,
            )
        self.assertEqual(code, 1)
        importer.assert_not_called()
        self.assertTrue(json.loads(output[0])["review_required"])
        self.assertTrue(json.loads(output[-1])["cancelled"])

    def test_skills_import_refuses_lint_errors_even_with_yes(self):
        output = []
        with patch("core.skill_linter.lint_skill", return_value=[]), patch(
            "core.skill_linter.findings_as_json",
            return_value={"valid": False, "errors": 1, "warnings": 0, "findings": []},
        ), patch("yuki_cli.install_skill_file") as importer:
            code = run_cli(
                ["skills", "import", "/tmp/release-notes", "--yes"],
                output_fn=output.append,
            )
        self.assertEqual(code, 1)
        importer.assert_not_called()
        self.assertIn("fix them before importing", json.loads(output[-1])["error"])

    def test_plugin_install_command_uses_validated_extension_store(self):
        output = []
        with patch("yuki_cli.install_extension", return_value={"id": "docs", "name": "docs", "path": "/tmp/docs.json"}) as install:
            code = run_cli(["plugins", "install", "/tmp/docs.json"], output_fn=output.append)
        self.assertEqual(code, 0)
        install.assert_called_once_with("/tmp/docs.json", "plugin")
        self.assertIn("no third-party code was executed", output[0])

    def test_sessions_cli_persists_and_lists_without_provider_initialization(self):
        import os
        import tempfile
        from pathlib import Path

        output = []
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"YUKI_HOME": directory}):
            created = run_cli(
                ["sessions", "create", "--workspace", "project-a", "--title", "Build Yuki"],
                output_fn=output.append,
            )
            self.assertEqual(created, 0)
            session_id = json.loads(output[-1])["session"]["id"]
            listed = run_cli(
                ["sessions", "list", "--workspace", "project-a"],
                output_fn=output.append,
            )
            self.assertEqual(listed, 0)
            self.assertEqual(json.loads(output[-1])["sessions"][0]["id"], session_id)
            shown = run_cli(
                ["sessions", "show", "--workspace", "project-a", session_id],
                output_fn=output.append,
            )
            self.assertEqual(shown, 0)
            self.assertEqual(json.loads(output[-1])["messages"], [])
            archived = run_cli(
                ["sessions", "archive", "--workspace", "project-a", session_id],
                output_fn=output.append,
            )
            self.assertEqual(archived, 0)
            self.assertEqual(json.loads(output[-1])["session"]["status"], "archived")
            self.assertTrue((Path(directory) / "conversations.sqlite3").is_file())

    def test_sessions_cli_rejects_cross_workspace_access(self):
        import os
        import tempfile

        output = []
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"YUKI_HOME": directory}):
            run_cli(["sessions", "create", "--workspace", "workspace-a"], output_fn=output.append)
            session_id = json.loads(output[-1])["session"]["id"]
            code = run_cli(
                ["sessions", "show", "--workspace", "workspace-b", session_id],
                output_fn=output.append,
            )
        self.assertEqual(code, 2)
        self.assertIn("failed safely", output[-1])

    def test_run_json_streams_correlated_ndjson_without_tools(self):
        output = []
        provider = FakeProvider(answer="Yuki says hello")
        code = run_cli(
            ["run", "hello", "--format", "json"],
            config_loader=lambda: {},
            provider_factory=lambda _: provider,
            output_fn=output.append,
        )
        events = [json.loads(line) for line in output]
        self.assertEqual(code, 0)
        self.assertEqual([event["type"] for event in events], ["start", "content", "done"])
        self.assertEqual(len({event["session_id"] for event in events}), 1)
        self.assertEqual(len({event["request_id"] for event in events}), 1)
        self.assertEqual(events[0]["data"]["tool_execution"], "disabled")
        self.assertEqual(events[1]["data"]["text"], "Yuki says hello")

    def test_run_json_reports_provider_failure_as_error_event(self):
        output = []
        provider = FakeProvider(available=False)
        code = run_cli(
            ["run", "hello", "--format", "json"],
            config_loader=lambda: {},
            provider_factory=lambda _: provider,
            output_fn=output.append,
        )
        events = [json.loads(line) for line in output]
        self.assertEqual(code, 3)
        self.assertEqual([event["type"] for event in events], ["start", "error", "done"])
        self.assertEqual(events[1]["data"]["code"], "provider_unavailable")
        self.assertEqual(events[-1]["data"]["status"], 3)

    def test_start_routes_to_integrated_guarded_runtime(self):
        with patch("main.main") as integrated_main:
            self.assertEqual(run_cli(["start"], output_fn=lambda _: None), 0)
        integrated_main.assert_called_once_with()

    def test_desktop_routes_to_gui_console(self):
        with patch("ui.desktop_app.run_desktop") as run_desktop:
            self.assertEqual(run_cli(["desktop"], output_fn=lambda _: None), 0)
        run_desktop.assert_called_once_with()

    def test_setup_never_downloads_model_without_explicit_confirmation(self):
        output = []
        with patch("yuki_cli.shutil.which", return_value="/usr/bin/ollama"), patch(
            "yuki_cli.subprocess.run", return_value=type("Result", (), {
                "returncode": 0, "stdout": "NAME ID SIZE MODIFIED\nother:latest abc 1GB today\n"
            })()
        ) as run:
            code = run_cli(
                ["setup"],
                config_loader=lambda: {"provider": "ollama", "ollama_model": "llama3.2:3b"},
                input_fn=lambda _: "n",
                output_fn=output.append,
            )
        self.assertEqual(code, 1)
        self.assertEqual(run.call_count, 1)
        self.assertEqual(run.call_args.args[0][-1], "list")
        self.assertIn("ollama pull llama3.2:3b", output[-1])

    def test_setup_does_not_treat_different_model_size_as_installed(self):
        output = []
        listed = type("Result", (), {
            "returncode": 0,
            "stdout": "NAME ID SIZE MODIFIED\nllama3.2:1b abc 1GB today\n",
        })()
        with patch("yuki_cli.shutil.which", return_value="/usr/bin/ollama"), patch(
            "yuki_cli.subprocess.run", return_value=listed
        ) as run:
            code = run_cli(
                ["setup"],
                config_loader=lambda: {"provider": "ollama", "ollama_model": "llama3.2:3b"},
                input_fn=lambda _: "n",
                output_fn=output.append,
            )
        self.assertEqual(code, 1)
        self.assertEqual(run.call_count, 1)
        self.assertIn("not listed", output[0])

    def test_setup_only_pulls_after_yes_confirmation(self):
        output = []
        results = [
            type("Result", (), {"returncode": 0, "stdout": "NAME ID SIZE MODIFIED\nother:latest abc 1GB today\n"})(),
            type("Result", (), {"returncode": 0, "stdout": "", "stderr": ""})(),
        ]
        with patch("yuki_cli.shutil.which", return_value="/usr/bin/ollama"), patch(
            "yuki_cli.subprocess.run", side_effect=results
        ) as run:
            code = run_cli(
                ["setup"],
                config_loader=lambda: {"provider": "ollama", "ollama_model": "llama3.2:3b"},
                input_fn=lambda _: "yes",
                output_fn=output.append,
            )
        self.assertEqual(code, 0)
        self.assertEqual(run.call_count, 2)
        self.assertEqual(run.call_args.args[0][-2:], ["pull", "llama3.2:3b"])
        self.assertIn("inference is not yet verified", output[-1])

    def test_setup_rejects_untrusted_model_command_characters(self):
        output = []
        code = run_cli(
            ["setup"],
            config_loader=lambda: {"provider": "ollama", "ollama_model": "model;del files"},
            output_fn=output.append,
        )
        self.assertEqual(code, 2)
        self.assertIn("invalid", output[-1].lower())

    def test_setup_preserves_explicit_cloud_provider_selection(self):
        output = []
        code = run_cli(
            ["setup"],
            config_loader=lambda: {"provider": "openai"},
            output_fn=output.append,
        )
        self.assertEqual(code, 0)
        self.assertTrue(any("No provider or cloud setting was changed." in line for line in output))

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

    def test_chat_carries_recent_session_context_between_turns(self):
        provider = FakeProvider(answer="first answer")
        output = []
        messages = iter(["first question", "follow-up question", "/quit"])
        code = run_cli(
            ["chat"],
            config_loader=lambda: {},
            provider_factory=lambda _config: provider,
            input_fn=lambda _prompt: next(messages),
            output_fn=output.append,
        )
        self.assertEqual(code, 0)
        self.assertEqual(len(provider.prompts), 2)
        self.assertIn("User: first question", provider.prompts[1])
        self.assertIn("Yuki: first answer", provider.prompts[1])
        self.assertIn("User: follow-up question", provider.prompts[1])

    def test_chat_context_is_bounded_to_recent_turns(self):
        from yuki_cli import CHAT_HISTORY_TURNS, _build_chat_prompt
        history = [(f"user-{i}", f"assistant-{i}") for i in range(CHAT_HISTORY_TURNS + 3)]
        prompt = _build_chat_prompt(history, "current")
        self.assertIn(f"user-{CHAT_HISTORY_TURNS + 2}", prompt)
        self.assertNotIn("user-0", prompt)
        self.assertIn("User: current", prompt)

    def test_chat_rejects_oversized_message_without_calling_provider(self):
        provider = FakeProvider()
        output = []
        messages = iter(["x" * 5000, "/quit"])
        code = run_cli(
            ["chat"],
            config_loader=lambda: {},
            provider_factory=lambda _config: provider,
            input_fn=lambda _prompt: next(messages),
            output_fn=output.append,
        )
        self.assertEqual(code, 0)
        self.assertEqual(provider.prompts, [])
        self.assertTrue(any("chat limit" in line for line in output))

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

    def test_skill_packs_list_reports_loaded_packs_and_validation_errors(self):
        output = []
        pack = type("Pack", (), {
            "name": "pack_release_notes", "pack_name": "release_notes", "description": "Draft release notes.",
            "risk": "low", "requires_approval": False,
        })()
        with patch("yuki_cli.load_installed_skill_packs", return_value=([pack], [])):
            code = run_cli(["skills", "packs", "list"], output_fn=output.append)
        self.assertEqual(code, 0)
        report = json.loads(output[0])
        self.assertEqual(report["installed"][0]["name"], "release_notes")
        self.assertFalse(report["installed"][0]["requires_approval"])

    def test_skill_pack_install_uses_validated_data_only_store(self):
        from pathlib import Path
        output = []
        with patch("yuki_cli.install_skill_pack", return_value=Path("/tmp/skill-packs/release_notes.json")) as install:
            code = run_cli(["skills", "packs", "install", "/tmp/release_notes.json"], output_fn=output.append)
        self.assertEqual(code, 0)
        install.assert_called_once_with("/tmp/release_notes.json")
        report = json.loads(output[0])
        self.assertEqual(report["installed"]["name"], "release_notes")
        self.assertIn("next Yuki runtime start", report["note"])

    def test_skill_pack_remove_returns_nonzero_when_missing(self):
        output = []
        with patch("yuki_cli.remove_skill_pack", return_value=False) as remove:
            code = run_cli(["skills", "packs", "remove", "release_notes"], output_fn=output.append)
        self.assertEqual(code, 1)
        remove.assert_called_once_with("release_notes")
        self.assertFalse(json.loads(output[0])["removed"])


if __name__ == "__main__":
    unittest.main()
