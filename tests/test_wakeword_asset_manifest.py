import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from training.wakeword.validate_assets import ManifestError, validate_manifest


class WakeWordAssetManifestTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "background").mkdir()
        self.asset = self.root / "background" / "room.wav"
        self.asset.write_bytes(b"licensed test audio bytes")
        self.manifest = self.root / "assets.manifest.json"
        self.entry = {
            "path": "background/room.wav",
            "role": "background_audio",
            "sha256": hashlib.sha256(self.asset.read_bytes()).hexdigest(),
            "license": "CC0-1.0",
            "license_url": "https://example.invalid/license",
            "source_url": "https://example.invalid/audio",
            "rights_reviewed": True,
            "allow_training": True,
            "allow_redistribution": False,
        }

    def tearDown(self):
        self.temp.cleanup()

    def write_manifest(self, assets=None):
        self.manifest.write_text(
            json.dumps({"schema_version": 1, "assets": [self.entry] if assets is None else assets}),
            encoding="utf-8",
        )

    def test_valid_training_asset_and_hash(self):
        self.write_manifest()
        self.assertEqual(validate_manifest(self.manifest), ["background/room.wav"])

    def test_hash_mismatch_is_rejected(self):
        self.asset.write_bytes(b"changed")
        self.write_manifest()
        with self.assertRaisesRegex(ManifestError, "SHA-256 mismatch"):
            validate_manifest(self.manifest)

    def test_unreviewed_or_unapproved_asset_is_rejected(self):
        for field, value in (("rights_reviewed", False), ("allow_training", False)):
            entry = dict(self.entry)
            entry[field] = value
            self.write_manifest([entry])
            with self.subTest(field=field), self.assertRaises(ManifestError):
                validate_manifest(self.manifest)

    def test_redistribution_requires_separate_approval(self):
        self.write_manifest()
        with self.assertRaisesRegex(ManifestError, "not approved for redistribution"):
            validate_manifest(self.manifest, require_redistribution=True)

    def test_path_traversal_is_rejected(self):
        entry = dict(self.entry, path="../outside.wav")
        self.write_manifest([entry])
        with self.assertRaisesRegex(ManifestError, "Unsafe asset path"):
            validate_manifest(self.manifest)

    def test_symlink_is_rejected(self):
        link = self.root / "background" / "linked.wav"
        link.symlink_to(self.asset)
        entry = dict(self.entry, path="background/linked.wav")
        self.write_manifest([entry])
        with self.assertRaisesRegex(ManifestError, "Symlinked"):
            validate_manifest(self.manifest)

    def test_duplicate_paths_are_rejected(self):
        self.write_manifest([self.entry, self.entry])
        with self.assertRaisesRegex(ManifestError, "Duplicate"):
            validate_manifest(self.manifest)

    def test_invalid_digest_is_rejected(self):
        entry = dict(self.entry, sha256="not-a-hash")
        self.write_manifest([entry])
        with self.assertRaisesRegex(ManifestError, "invalid SHA-256"):
            validate_manifest(self.manifest)


if __name__ == "__main__":
    unittest.main()
