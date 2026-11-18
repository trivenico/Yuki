import tempfile
import unittest
from pathlib import Path

from voice.secure_artifact_store import (
    EncryptedVoiceArtifactStore,
    VoiceArtifactAuthorizationError,
    VoiceArtifactStoreError,
)


class EncryptedVoiceArtifactStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.authorized = True
        self.key = b"\x11" * 32
        self.directory = Path(self.temp.name) / "artifacts"
        self.store = EncryptedVoiceArtifactStore(
            self.directory, key=self.key, authorize=lambda: self.authorized,
            max_artifact_bytes=1024,
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_round_trip_persists_only_ciphertext(self):
        sample = b"RIFF" + b"private voice sample" * 10
        artifact_id = self.store.put(sample, source_owner_consent=True)
        path = self.directory / (artifact_id + ".ykv")
        stored = path.read_bytes()
        self.assertEqual(self.store.get(artifact_id), sample)
        self.assertNotIn(sample, stored)
        self.assertTrue(stored.startswith(b"YKV1"))
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_rejects_missing_consent_empty_and_oversized_audio(self):
        with self.assertRaises(VoiceArtifactAuthorizationError):
            self.store.put(b"sample", source_owner_consent=False)
        with self.assertRaises(ValueError):
            self.store.put(b"", source_owner_consent=True)
        with self.assertRaises(VoiceArtifactStoreError):
            self.store.put(b"x" * 1025, source_owner_consent=True)

    def test_requires_authenticated_session_for_every_operation(self):
        artifact_id = self.store.put(b"sample", source_owner_consent=True)
        self.authorized = False
        with self.assertRaises(VoiceArtifactAuthorizationError):
            self.store.get(artifact_id)
        with self.assertRaises(VoiceArtifactAuthorizationError):
            self.store.delete(artifact_id, confirmed=True)

    def test_delete_requires_confirmation_and_removes_artifact(self):
        artifact_id = self.store.put(b"sample", source_owner_consent=True)
        with self.assertRaises(VoiceArtifactAuthorizationError):
            self.store.delete(artifact_id, confirmed=False)
        self.store.delete(artifact_id, confirmed=True)
        with self.assertRaises(VoiceArtifactStoreError):
            self.store.get(artifact_id)

    def test_wrong_key_and_tampering_fail_closed(self):
        artifact_id = self.store.put(b"sample", source_owner_consent=True)
        wrong = EncryptedVoiceArtifactStore(
            self.directory, key=b"\x22" * 32, authorize=lambda: True,
            max_artifact_bytes=1024,
        )
        with self.assertRaises(VoiceArtifactStoreError):
            wrong.get(artifact_id)
        path = self.directory / (artifact_id + ".ykv")
        payload = bytearray(path.read_bytes())
        payload[-1] ^= 1
        path.write_bytes(payload)
        with self.assertRaises(VoiceArtifactStoreError):
            self.store.get(artifact_id)

    def test_rejects_symlink_directory_and_invalid_key(self):
        root = Path(self.temp.name)
        target = root / "target"
        target.mkdir()
        link = root / "linked"
        link.symlink_to(target, target_is_directory=True)
        with self.assertRaises(VoiceArtifactStoreError):
            EncryptedVoiceArtifactStore(link, key=self.key, authorize=lambda: True)
        with self.assertRaises(ValueError):
            EncryptedVoiceArtifactStore(root / "bad", key=b"short", authorize=lambda: True)

    def test_rejects_invalid_artifact_identifier(self):
        with self.assertRaises(ValueError):
            self.store.get("../outside")


if __name__ == "__main__":
    unittest.main()
