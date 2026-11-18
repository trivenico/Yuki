import base64
import unittest

from voice.key_store import (
    UnsupportedCredentialBackendError,
    VoiceEncryptionKeyProvider,
    VoiceKeyStoreError,
)


class _NativeBackend:
    priority = 1


_NativeBackend.__module__ = "keyring.backends.SecretService"


class _PlaintextBackend:
    priority = 1


_PlaintextBackend.__module__ = "keyrings.alt.file"


class _FakeKeyring:
    def __init__(self, backend=None):
        self.backend = backend or _NativeBackend()
        self.values = {}

    def get_keyring(self):
        return self.backend

    def get_password(self, service, account):
        return self.values.get((service, account))

    def set_password(self, service, account, value):
        self.values[(service, account)] = value

    def delete_password(self, service, account):
        del self.values[(service, account)]


class VoiceEncryptionKeyProviderTests(unittest.TestCase):
    def test_creates_and_reuses_user_scoped_32_byte_key(self):
        backend = _FakeKeyring()
        first = VoiceEncryptionKeyProvider("user-123", keyring_module=backend)
        second = VoiceEncryptionKeyProvider("user-123", keyring_module=backend)
        key = first.get_or_create_key()
        self.assertEqual(len(key), 32)
        self.assertEqual(second.get_or_create_key(), key)
        self.assertEqual(len(backend.values), 1)

    def test_scopes_keys_per_user_without_storing_raw_identity(self):
        backend = _FakeKeyring()
        first = VoiceEncryptionKeyProvider("user-one", keyring_module=backend)
        second = VoiceEncryptionKeyProvider("user-two", keyring_module=backend)
        self.assertNotEqual(first.get_or_create_key(), second.get_or_create_key())
        account = next(iter(backend.values))[1]
        self.assertNotIn("user-one", account)
        self.assertNotIn("user-two", account)

    def test_rejects_plaintext_or_unrecognized_backend(self):
        provider = VoiceEncryptionKeyProvider(
            "user", keyring_module=_FakeKeyring(_PlaintextBackend()),
        )
        with self.assertRaises(UnsupportedCredentialBackendError):
            provider.get_or_create_key()

    def test_rejects_invalid_stored_key(self):
        backend = _FakeKeyring()
        backend.values[("Yuki.VoiceArtifacts.v1", __import__("hashlib").sha256(b"user").hexdigest())] = "bad"
        provider = VoiceEncryptionKeyProvider("user", keyring_module=backend)
        with self.assertRaises(VoiceKeyStoreError):
            provider.get_or_create_key()

    def test_key_deletion_requires_confirmation_and_is_verified(self):
        backend = _FakeKeyring()
        provider = VoiceEncryptionKeyProvider("user", keyring_module=backend)
        provider.get_or_create_key()
        with self.assertRaises(VoiceKeyStoreError):
            provider.delete_key(confirmed=False)
        provider.delete_key(confirmed=True)
        self.assertEqual(backend.values, {})

    def test_requires_non_empty_user_scope(self):
        with self.assertRaises(ValueError):
            VoiceEncryptionKeyProvider(" ", keyring_module=_FakeKeyring())


if __name__ == "__main__":
    unittest.main()
