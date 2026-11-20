import json
import os
import tempfile
import unittest
from pathlib import Path

from ui.desktop.onboarding import DesktopOnboarding, OnboardingStateError


class DesktopOnboardingTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "profile" / "onboarding.json"

    def test_steps_must_be_completed_sequentially(self):
        onboarding = DesktopOnboarding(self.path)
        with self.assertRaises(OnboardingStateError):
            onboarding.complete_step("provider", accepted=True, provider="native")
        self.assertEqual(onboarding.current_step, "welcome")

    def test_explicit_progress_persists_across_instances(self):
        onboarding = DesktopOnboarding(self.path)
        onboarding.complete_step("welcome", accepted=True)
        onboarding.complete_step("readiness", accepted=True)
        onboarding.complete_step("provider", accepted=True, provider="defer")
        reopened = DesktopOnboarding(self.path)
        self.assertEqual(reopened.current_step, "privacy")
        self.assertEqual(reopened.snapshot()["provider_choice"], "defer")

    def test_full_onboarding_requires_each_explicit_step(self):
        onboarding = DesktopOnboarding(self.path)
        for step in ("welcome", "readiness"):
            onboarding.complete_step(step, accepted=True)
        onboarding.complete_step("provider", accepted=True, provider="native")
        for step in ("privacy", "permissions", "first_conversation"):
            onboarding.complete_step(step, accepted=True)
        self.assertTrue(onboarding.is_complete)
        self.assertIsNone(onboarding.current_step)

    def test_rejects_implicit_acceptance_and_invalid_provider(self):
        onboarding = DesktopOnboarding(self.path)
        with self.assertRaises(ValueError):
            onboarding.complete_step("welcome", accepted=False)
        onboarding.complete_step("welcome", accepted=True)
        onboarding.complete_step("readiness", accepted=True)
        with self.assertRaises(ValueError):
            onboarding.complete_step("provider", accepted=True, provider="mock")

    def test_corrupt_state_is_not_silently_reset(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text('{"schema_version": 99}', encoding="utf-8")
        with self.assertRaises(OnboardingStateError):
            DesktopOnboarding(self.path)
        self.assertEqual(json.loads(self.path.read_text())["schema_version"], 99)

    def test_non_sequential_tampering_is_rejected(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text(json.dumps({
            "schema_version": 1,
            "completed_steps": ["welcome", "provider"],
            "provider_choice": "native",
        }), encoding="utf-8")
        with self.assertRaises(OnboardingStateError):
            DesktopOnboarding(self.path)

    def test_symlink_state_is_rejected(self):
        self.path.parent.mkdir(parents=True)
        target = self.path.parent / "target.json"
        target.write_text("{}", encoding="utf-8")
        try:
            self.path.symlink_to(target)
        except (OSError, NotImplementedError):
            self.skipTest("symlink creation is unavailable")
        with self.assertRaises(OnboardingStateError):
            DesktopOnboarding(self.path)

    def test_reset_requires_confirmation(self):
        onboarding = DesktopOnboarding(self.path)
        onboarding.complete_step("welcome", accepted=True)
        with self.assertRaises(ValueError):
            onboarding.reset()
        onboarding.reset(confirmed=True)
        self.assertEqual(onboarding.current_step, "welcome")


if __name__ == "__main__":
    unittest.main()
