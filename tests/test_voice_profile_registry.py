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
            self.registry.activate_profile("clone-one", confirmed=True)
        self.registry.approve_profile("clone-one")
        with self.assertRaises(VoiceProfileAuthorizationError):
            self.registry.activate_profile("clone-one", confirmed=False)
        self.registry.activate_profile("clone-one", confirmed=True)
        self.assertEqual(self.registry.active_profile()["profile_id"], "clone-one")

    def test_revert_restores_previous_default(self):
        self.stage()
        self.registry.approve_profile("clone-one")
        self.registry.activate_profile("clone-one", confirmed=True)
        self.registry.stage_profile(
            "clone-two", "Second voice", "local-test", "artifact:two",
            source_owner_consent=True,
        )
        self.registry.approve_profile("clone-two")
        self.registry.activate_profile("clone-two", confirmed=True)
        self.assertEqual(self.registry.active_profile()["profile_id"], "clone-two")
        self.assertEqual(self.registry.revert_to_previous(confirmed=True)["profile_id"], "clone-one")
        self.assertEqual(self.registry.active_profile()["profile_id"], "clone-one")

    def test_cannot_disable_or_delete_active_or_previous_profile(self):
        self.stage()
        self.registry.approve_profile("clone-one")
        self.registry.activate_profile("clone-one", confirmed=True)
        with self.assertRaises(VoiceProfileRegistryError):
            self.registry.disable_profile("clone-one", confirmed=True)
        with self.assertRaises(VoiceProfileRegistryError):
            self.registry.delete_profile("clone-one", confirmed=True)

    def test_previous_voice_cannot_be_disabled_before_rollback(self):
        self.stage()
        self.registry.approve_profile("clone-one")
        self.registry.activate_profile("clone-one", confirmed=True)
        self.registry.stage_profile(
            "clone-two", "Second voice", "local-test", "artifact:two",
            source_owner_consent=True,
        )
        self.registry.approve_profile("clone-two")
        self.registry.activate_profile("clone-two", confirmed=True)
        with self.assertRaises(VoiceProfileRegistryError):
            self.registry.disable_profile("clone-one", confirmed=True)
        self.assertEqual(self.registry.revert_to_previous(confirmed=True)["profile_id"], "clone-one")

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

    def test_rejects_disabled_active_or_previous_profiles_on_load(self):
        self.stage()
        self.registry.approve_profile("clone-one")
        self.registry.activate_profile("clone-one", confirmed=True)
        registry_file = Path(self.temp.name) / "voices" / "profiles.json"
        import json
        state = json.loads(registry_file.read_text(encoding="utf-8"))
        state["profiles"]["clone-one"]["status"] = "disabled"
        registry_file.write_text(json.dumps(state), encoding="utf-8")
        with self.assertRaises(VoiceProfileRegistryError):
            VoiceProfileRegistry(Path(self.temp.name) / "voices", authorize=lambda: True)

    def test_posix_directory_handle_prevents_registry_path_swap(self):
        import os
        if os.name != "posix" or os.open not in os.supports_dir_fd or os.rename not in os.supports_dir_fd:
            self.skipTest("POSIX directory-relative operations are unavailable.")
        original = Path(self.temp.name) / "voices"
        moved = Path(self.temp.name) / "moved-voices"
        attacker = Path(self.temp.name) / "attacker-voices"
        attacker.mkdir(mode=0o700)
        original.rename(moved)
        original.symlink_to(attacker, target_is_directory=True)
        self.stage("after-swap")
        self.assertTrue((moved / "profiles.json").is_file())
        self.assertFalse((attacker / "profiles.json").exists())
        persisted = VoiceProfileRegistry(moved, authorize=lambda: True)
        self.assertEqual(persisted.list_profiles()[0]["profile_id"], "after-swap")

    def test_rejects_invalid_profile_ids_and_duplicate_profiles(self):
        with self.assertRaises(ValueError):
            self.registry.stage_profile(
                "../bad", "Bad", "local", "artifact:bad", source_owner_consent=True,
            )
        self.stage()
        with self.assertRaises(VoiceProfileRegistryError):
            self.stage()


if __name__ == "__main__":
    unittest.main()
