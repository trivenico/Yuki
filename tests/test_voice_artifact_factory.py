import tempfile
import unittest
from pathlib import Path

from voice.artifact_factory import create_user_voice_artifact_store
from voice.key_store import VoiceKeyStoreError


class _Backend:
    priority = 1


_Backend.__module__ = "keyring.backends.Windows"


class _Keyring:
    def __init__(self):
        self.values = {}

    def get_keyring(self):
        return _Backend()

    def get_password(self, service, account):
        return self.values.get((service, account))

    def set_password(self, service, account, value):
        self.values[(service, account)] = value

    def delete_password(self, service, account):
        del self.values[(service, account)]


class VoiceArtifactFactoryTests(unittest.TestCase):
    def test_factory_connects_os_key_store_to_encrypted_store(self):
        with tempfile.TemporaryDirectory() as temp:
            keyring = _Keyring()
            authorized = True
            store = create_user_voice_artifact_store(
                Path(temp) / "voice",
                user_scope="authenticated-user",
                authorize=lambda: authorized,
                keyring_module=keyring,
                max_artifact_bytes=1024,
            )
            artifact_id = store.put(b"sample", source_owner_consent=True)
            self.assertEqual(store.get(artifact_id), b"sample")
            self.assertEqual(len(keyring.values), 1)

    def test_factory_fails_closed_for_unsupported_backend(self):
        class PlainBackend:
            priority = 1
        PlainBackend.__module__ = "keyrings.alt.file"

        class PlainKeyring(_Keyring):
            def get_keyring(self):
                return PlainBackend()

        with tempfile.TemporaryDirectory() as temp:
            with self.assertRaises(VoiceKeyStoreError):
                create_user_voice_artifact_store(
                    Path(temp) / "voice",
                    user_scope="authenticated-user",
                    authorize=lambda: True,
                    keyring_module=PlainKeyring(),
                )


if __name__ == "__main__":
    unittest.main()
