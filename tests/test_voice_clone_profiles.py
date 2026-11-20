import json
import os
import tempfile
import unittest
from pathlib import Path

from voice.clone_profiles import (
    VoiceProfileRegistry,
    VoiceProfileRegistryError,
)


class VoiceProfileRegistryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "voices"
        self.root.mkdir()
        self.model = self.root / "profile-a"
        self.model.mkdir()
        (self.model / "model.bin").write_bytes(b"fixture")
        self.registry = VoiceProfileRegistry(self.root)

    def add(self, **overrides):
        values = {
            "profile_id": "my-voice",
            "display_name": "My voice",
            "engine": "future-local-engine",
            "model_directory": self.model,
            "source_kind": "user_owned",
            "source_authorized": True,
        }
        values.update(overrides)
        return self.registry.add(**values)

    def test_add_and_list_stores_only_metadata(self):
        profile = self.add()
        self.assertEqual(profile.profile_id, "my-voice")
        self.assertEqual(self.registry.list_profiles(), (profile,))
        raw = json.loads((self.root / "profiles.json").read_text())
        self.assertNotIn("audio", raw)
        self.assertNotIn("transcript", raw)
        self.assertEqual(raw["profiles"][0]["model_directory"], "profile-a")

    def test_source_authorization_is_required(self):
        with self.assertRaisesRegex(VoiceProfileRegistryError, "authorization"):
            self.add(source_authorized=False)
        self.assertEqual(self.registry.list_profiles(), ())

    def test_unknown_source_kind_rejected(self):
        with self.assertRaises(ValueError):
            self.add(source_kind="unverified")

    def test_model_directory_must_be_inside_private_root(self):
        outside = Path(self.temp.name) / "outside"
        outside.mkdir()
        with self.assertRaisesRegex(VoiceProfileRegistryError, "inside"):
            self.add(model_directory=outside)

    def test_model_directory_must_exist_and_be_directory(self):
        with self.assertRaises((FileNotFoundError, OSError)):
            self.add(model_directory=self.root / "missing")

    def test_default_selection_requires_registered_profile(self):
        with self.assertRaisesRegex(VoiceProfileRegistryError, "not registered"):
            self.registry.set_default("my-voice")
        self.add()
        self.registry.set_default("my-voice")
        self.assertEqual(self.registry.default_profile_id(), "my-voice")

    def test_removing_default_clears_default_but_does_not_delete_model(self):
        self.add()
        self.registry.set_default("my-voice")
        self.registry.remove("my-voice")
        self.assertIsNone(self.registry.default_profile_id())
        self.assertTrue((self.model / "model.bin").is_file())

    def test_duplicate_profile_rejected(self):
        self.add()
        with self.assertRaisesRegex(VoiceProfileRegistryError, "already exists"):
            self.add()

    def test_manifest_permissions_are_private_on_posix(self):
        self.add()
        if os.name != "nt":
            self.assertEqual((self.root / "profiles.json").stat().st_mode & 0o777, 0o600)
            self.assertEqual(self.root.stat().st_mode & 0o777, 0o700)

    def test_corrupt_manifest_fails_closed(self):
        (self.root / "profiles.json").write_text("{", encoding="utf-8")
        with self.assertRaisesRegex(VoiceProfileRegistryError, "unreadable"):
            self.registry.add(
                profile_id="another", display_name="Another", engine="engine",
                model_directory=self.model, source_kind="synthetic", source_authorized=True,
            )


if __name__ == "__main__":
    unittest.main()
