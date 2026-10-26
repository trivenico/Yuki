import json
import tempfile
import unittest
from pathlib import Path

from core.desktop_onboarding import (
    ONBOARDING_STEPS,
    build_desktop_readiness,
    complete_onboarding_step,
    load_onboarding_state,
    reset_onboarding,
)


class DesktopOnboardingTests(unittest.TestCase):
    def test_progress_is_resumable_and_completes_only_after_all_steps(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "state" / "onboarding.json"
            state = complete_onboarding_step(path, "welcome")
            self.assertEqual(state["completed_steps"], ["welcome"])
            state = complete_onboarding_step(path, "provider", selected_provider="OpenAI", provider_verified=True)
            self.assertEqual(state["selected_provider"], "openai")
            self.assertEqual(load_onboarding_state(path), state)
            for step in ONBOARDING_STEPS[2:]:
                state = complete_onboarding_step(path, step)
            self.assertTrue(state["completed"])
            self.assertEqual(state["completed_steps"], list(ONBOARDING_STEPS))

    def test_steps_cannot_be_skipped_or_unknown(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "onboarding.json"
            with self.assertRaises(ValueError):
                complete_onboarding_step(path, "permissions")
            with self.assertRaises(ValueError):
                complete_onboarding_step(path, "unknown")
            with self.assertRaises(ValueError):
                complete_onboarding_step(path, "provider", selected_provider="openai")

    def test_provider_step_requires_verified_connectivity(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "onboarding.json"
            complete_onboarding_step(path, "welcome")
            with self.assertRaises(ValueError):
                complete_onboarding_step(path, "provider", selected_provider="openai")
            state = complete_onboarding_step(
                path, "provider", selected_provider="openai", provider_verified=True
            )
            self.assertEqual(state["selected_provider"], "openai")

    def test_provider_step_cannot_be_marked_complete_without_provider_name(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "onboarding.json"
            complete_onboarding_step(path, "welcome")
            with self.assertRaises(ValueError):
                complete_onboarding_step(path, "provider", provider_verified=True)

    def test_corrupt_out_of_order_state_cannot_skip_setup_gates(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "onboarding.json"
            path.write_text(json.dumps({
                "schema_version": 1,
                "completed_steps": ["permissions", "welcome", "first_conversation"],
                "selected_provider": "OPENAI",
                "completed": True,
            }), encoding="utf-8")
            state = load_onboarding_state(path)
            self.assertEqual(state["completed_steps"], ["welcome"])
            self.assertIsNone(state["selected_provider"])
            with self.assertRaises(ValueError):
                complete_onboarding_step(path, "permissions")

    def test_state_never_persists_credentials_or_arbitrary_fields(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "onboarding.json"
            complete_onboarding_step(path, "welcome")
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("api_key", text)
            self.assertNotIn("secret", text)
            # Unknown fields are discarded during normalization.
            path.write_text(json.dumps({
                "schema_version": 1,
                "completed_steps": ["welcome"],
                "selected_provider": None,
                "completed": False,
                "api_key": "super-secret-value",
            }), encoding="utf-8")
            state = load_onboarding_state(path)
            self.assertNotIn("api_key", state)
            self.assertNotIn("super-secret-value", json.dumps(state))

    def test_corrupt_and_oversized_state_fail_closed(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "onboarding.json"
            path.write_text("{bad json", encoding="utf-8")
            self.assertEqual(load_onboarding_state(path)["completed_steps"], [])
            path.write_text(" " * (16 * 1024 + 1), encoding="utf-8")
            self.assertEqual(load_onboarding_state(path)["completed_steps"], [])

    def test_reset_clears_progress_without_claiming_credentials_were_deleted(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "onboarding.json"
            complete_onboarding_step(path, "welcome")
            state = reset_onboarding(path)
            self.assertEqual(state["completed_steps"], [])
            self.assertFalse(state["completed"])

    def test_readiness_report_is_sanitized_and_reports_workspace(self):
        with tempfile.TemporaryDirectory() as folder:
            class Manager:
                def list_providers(self):
                    return [
                        {"name": "openai", "implemented": True, "selected": True,
                         "api_key": "secret-do-not-leak"},
                        {"name": "planned", "implemented": False, "selected": False},
                        {"name": "", "implemented": True, "selected": False},
                    ]
            report = build_desktop_readiness(Manager(), workspace=folder)
            self.assertEqual(report["selected_provider"], "openai")
            self.assertTrue(report["workspace"]["exists"])
            self.assertTrue(report["workspace"]["writable"])
            serialized = json.dumps(report)
            self.assertNotIn("secret-do-not-leak", serialized)
            self.assertNotIn("api_key", serialized)
            self.assertFalse(report["credentials_included"])

    def test_readiness_provider_failure_is_safe(self):
        class BrokenManager:
            def list_providers(self):
                raise RuntimeError("private exception with secret=do-not-leak")
        report = build_desktop_readiness(BrokenManager(), workspace="/path/that/does/not/exist")
        self.assertEqual(report["providers"], [])
        self.assertIsNone(report["selected_provider"])
        self.assertNotIn("do-not-leak", json.dumps(report))


if __name__ == "__main__":
    unittest.main()
