import tempfile
import unittest
from pathlib import Path

from voice.profile_registry import (
    VoiceProfileAuthorizationError,
    VoiceProfileRegistry,
    VoiceProfileRegistryError,
)


class VoiceProfileRegistryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.authorized = True
        self.registry = VoiceProfileRegistry(
            Path(self.temp.name) / "voices",
            authorize=lambda: self.authorized,
        )

    def tearDown(self):
        self.temp.cleanup()

    def stage(self, profile_id="clone-one"):
        return self.registry.stage_profile(
            profile_id, "My voice", "local-test", "artifact:clone-one",
            source_owner_consent=True,
        )

    def test_requires_authenticated_session_for_reads_and_writes(self):
        self.authorized = False
        with self.assertRaises(VoiceProfileAuthorizationError):
            self.registry.list_profiles()
        with self.assertRaises(VoiceProfileAuthorizationError):
            self.stage()

    def test_requires_explicit_source_owner_consent(self):
        with self.assertRaises(VoiceProfileAuthorizationError):
            self.registry.stage_profile(
                "clone-one", "My voice", "local-test", "artifact:one",
                source_owner_consent=False,
            )
        self.assertEqual(self.registry.list_profiles(), [])

    def test_staged_profile_must_be_approved_before_activation(self):
        self.stage()
        with self.assertRaises(VoiceProfileRegistryError):
            self.registry.activate_profile("clone-one")
        self.registry.approve_profile("clone-one")
        self.registry.activate_profile("clone-one")
        self.assertEqual(self.registry.active_profile()["profile_id"], "clone-one")

    def test_revert_restores_previous_default(self):
        self.stage()
        self.registry.approve_profile("clone-one")
        self.registry.activate_profile("clone-one")
        self.registry.stage_profile(
            "clone-two", "Second voice", "local-test", "artifact:two",
            source_owner_consent=True,
        )
        self.registry.approve_profile("clone-two")
        self.registry.activate_profile("clone-two")
        self.assertEqual(self.registry.active_profile()["profile_id"], "clone-two")
        self.assertEqual(self.registry.revert_to_previous()["profile_id"], "clone-one")
        self.assertEqual(self.registry.active_profile()["profile_id"], "clone-one")

    def test_cannot_disable_or_delete_active_or_previous_profile(self):
        self.stage()
        self.registry.approve_profile("clone-one")
        self.registry.activate_profile("clone-one")
        with self.assertRaises(VoiceProfileRegistryError):
            self.registry.disable_profile("clone-one")
        with self.assertRaises(VoiceProfileRegistryError):
            self.registry.delete_profile("clone-one")

    def test_profile_metadata_survives_restart_without_audio_bytes(self):
        self.stage()
        restarted = VoiceProfileRegistry(
            Path(self.temp.name) / "voices", authorize=lambda: True,
        )
        profile = restarted.list_profiles()[0]
        self.assertEqual(profile["artifact_ref"], "artifact:clone-one")
        self.assertNotIn("audio", profile)
        registry_file = Path(self.temp.name) / "voices" / "profiles.json"
        self.assertNotIn("RIFF", registry_file.read_text(encoding="utf-8"))

    def test_rejects_symlink_registry_directory(self):
        root = Path(self.temp.name)
        target = root / "target"
        target.mkdir()
        link = root / "linked"
        link.symlink_to(target, target_is_directory=True)
        with self.assertRaises(VoiceProfileRegistryError):
            VoiceProfileRegistry(link, authorize=lambda: True)

    def test_rejects_invalid_profile_ids_and_duplicate_profiles(self):
        with self.assertRaises(ValueError):
            self.registry.stage_profile(
                "../bad", "Bad", "local", "artifact:bad", source_owner_consent=True,
            )
        self.stage()
        with self.assertRaises(VoiceProfileRegistryError):
            self.stage()


if __.name__ == "__main__":
    unittest.main()
