import hashlib
import unittest

from providers.model_artifact_manifest import (
    ModelArtifactManifestError,
    canonical_manifest_json,
    sha256_file,
    validate_manifest,
    verify_artifact,
)


def manifest(digest):
    return {
        "schema_version": 1,
        "model_id": "yuki/core",
        "model_version": "0.1.0",
        "artifact_sha256": digest,
        "source": "yuki_training_pipeline",
        "license_id": "Apache-2.0",
        "base_model": "opensource/base-model",
        "training_method": "lora",
        "dataset_sha256": ["a" * 64],
        "created_at": "2026-11-05T09:00:00Z",
    }


class ModelArtifactManifestTests(unittest.TestCase):
    def test_hashes_artifact_and_verifies_manifest(self):
        with self.subTest("artifact hash and manifest verification"):
            from tempfile import TemporaryDirectory
            from pathlib import Path

            with TemporaryDirectory() as directory:
                artifact = Path(directory) / "weights.bin"
                artifact.write_bytes(b"yuki-checkpoint")
                digest = hashlib.sha256(b"yuki-checkpoint").hexdigest()
                item = manifest(digest)
                self.assertEqual(sha256_file(artifact), digest)
                self.assertTrue(verify_artifact(artifact, item))
                self.assertEqual(validate_manifest(item)["provenance_review"], "unverified")

    def test_rejects_digest_mismatch(self):
        from tempfile import TemporaryDirectory
        from pathlib import Path

        with TemporaryDirectory() as directory:
            artifact = Path(directory) / "weights.bin"
            artifact.write_bytes(b"actual")
            self.assertFalse(verify_artifact(artifact, manifest("0" * 64)))

    def test_rejects_invalid_manifest_fields(self):
        changes = [
            {"artifact_sha256": "not-a-digest"},
            {"license_id": "unknown license with spaces"},
            {"training_method": "automatic_activation"},
            {"training_method": []},
            {"unexpected": "field"},
            {"dataset_sha256": ["bad"]},
            {"created_at": "yesterday"},
            {"created_at": "2026-11-05T09:00:00+04:00Z"},
            {"model_id": "../escape"},
        ]
        for change in changes:
            with self.subTest(change=change):
                item = manifest("a" * 64)
                item.update(change)
                with self.assertRaises(ModelArtifactManifestError):
                    validate_manifest(item)

    def test_rejects_duplicate_dataset_digests(self):
        item = manifest("a" * 64)
        item["dataset_sha256"] = ["b" * 64, "B" * 64]
        with self.assertRaisesRegex(ModelArtifactManifestError, "duplicate_dataset_digest"):
            validate_manifest(item)

    def test_rejects_symlink_artifact(self):
        from tempfile import TemporaryDirectory
        from pathlib import Path

        with TemporaryDirectory() as directory:
            target = Path(directory) / "target.bin"
            target.write_bytes(b"weights")
            link = Path(directory) / "link.bin"
            try:
                link.symlink_to(target)
            except (OSError, NotImplementedError):
                self.skipTest("symlinks unavailable")
            with self.assertRaisesRegex(ModelArtifactManifestError, "symlink"):
                sha256_file(link)

    def test_rejects_invalid_max_bytes(self):
        from tempfile import TemporaryDirectory
        from pathlib import Path

        with TemporaryDirectory() as directory:
            artifact = Path(directory) / "weights.bin"
            artifact.write_bytes(b"weights")
            for value in (True, 0, -1, 1.5, "10"):
                with self.subTest(value=value):
                    with self.assertRaisesRegex(ModelArtifactManifestError, "invalid_max_bytes"):
                        sha256_file(artifact, max_bytes=value)

    def test_rejects_artifact_over_size_limit(self):
        from tempfile import TemporaryDirectory
        from pathlib import Path

        with TemporaryDirectory() as directory:
            artifact = Path(directory) / "weights.bin"
            artifact.write_bytes(b"weights")
            with self.assertRaisesRegex(ModelArtifactManifestError, "artifact_size_out_of_bounds"):
                sha256_file(artifact, max_bytes=3)

    def test_manifest_serialization_is_stable(self):
        item = manifest("C" * 64)
        self.assertEqual(
            canonical_manifest_json(item),
            canonical_manifest_json(dict(reversed(list(item.items())))),
        )
        self.assertIn('"provenance_review":"unverified"', canonical_manifest_json(item))


if __name__ == "__main__":
    unittest.main()
