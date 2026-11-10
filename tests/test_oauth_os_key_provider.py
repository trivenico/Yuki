import base64
import unittest

from core.oauth_os_key_provider import OSKeyringOAuthKeyProvider, OAuthOSKeyStoreError


class MemoryCredentialBackend:
    def __init__(self):
        self.values = {}
        self.fail_reads = False
        self.fail_writes = False

    def get_password(self, service, username):
        if self.fail_reads:
            raise RuntimeError("backend detail")
        return self.values.get((service, username))

    def set_password(self, service, username, password):
        if self.fail_writes:
            raise RuntimeError("backend detail")
        self.values[(service, username)] = password


class OSKeyringOAuthKeyProviderTests(unittest.TestCase):
    def setUp(self):
        self.backend = MemoryCredentialBackend()
        self.provider = OSKeyringOAuthKeyProvider(service_name="test-yuki", backend=self.backend)

    def test_explicit_initialization_and_stable_keys(self):
        with self.assertRaisesRegex(OAuthOSKeyStoreError, "not_initialized"):
            self.provider.current_key()
        self.provider.initialize()
        key_id, key = self.provider.current_key()
        self.assertEqual(len(key), 32)
        self.assertEqual(self.provider.get_key(key_id), key)
        self.assertEqual(len(self.provider.hmac_key()), 32)
        restarted = OSKeyringOAuthKeyProvider(service_name="test-yuki", backend=self.backend)
        self.assertEqual(restarted.current_key(), (key_id, key))
        self.assertEqual(restarted.hmac_key(), self.provider.hmac_key())

    def test_rotation_retains_historical_keys(self):
        self.provider.initialize()
        old_id, old_key = self.provider.current_key()
        new_id = self.provider.rotate()
        active_id, active_key = self.provider.current_key()
        self.assertNotEqual(old_id, new_id)
        self.assertEqual(active_id, new_id)
        self.assertNotEqual(old_key, active_key)
        self.assertEqual(self.provider.get_key(old_id), old_key)
        self.assertEqual(set(self.provider.key_ids()), {old_id, new_id})

    def test_missing_active_key_fails_closed_without_replacement(self):
        self.provider.initialize()
        self.backend.values.pop((self.provider._service, self.provider._USERNAME_ACTIVE))
        with self.assertRaisesRegex(OAuthOSKeyStoreError, "integrity_failure"):
            self.provider.current_key()
        self.assertIsNone(self.backend.values.get((self.provider._service, self.provider._USERNAME_ACTIVE)))

    def test_interrupted_bootstrap_is_not_silently_reinitialized(self):
        self.backend.values[(self.provider._service, self.provider._USERNAME_MARKER)] = "initializing"
        with self.assertRaisesRegex(OAuthOSKeyStoreError, "initialization_incomplete"):
            self.provider.initialize()

    def test_corrupt_hmac_and_missing_historical_key_fail_closed(self):
        self.provider.initialize()
        old_id, _ = self.provider.current_key()
        self.backend.values[(self.provider._service, self.provider._USERNAME_HMAC)] = base64.b64encode(b"x").decode()
        with self.assertRaisesRegex(OAuthOSKeyStoreError, "integrity_failure"):
            self.provider.hmac_key()
        self.backend.values[(self.provider._service, self.provider._USERNAME_HMAC)] = base64.b64encode(b"h" * 32).decode()
        self.backend.values.pop((self.provider._service, self.provider._KEY_PREFIX + old_id))
        with self.assertRaisesRegex(OAuthOSKeyStoreError, "historical_key_unavailable"):
            self.provider.get_key(old_id)

    def test_backend_errors_are_sanitized(self):
        self.provider.initialize()
        self.backend.fail_reads = True
        with self.assertRaisesRegex(OAuthOSKeyStoreError, "read_failed") as raised:
            self.provider.current_key()
        self.assertNotIn("backend detail", str(raised.exception))

    def test_backend_write_failure_does_not_publish_ready_state(self):
        self.backend.fail_writes = True
        with self.assertRaisesRegex(OAuthOSKeyStoreError, "write_failed"):
            self.provider.initialize()
        self.assertNotEqual(
            self.backend.values.get((self.provider._service, self.provider._USERNAME_MARKER)),
            "ready",
        )


if __name__ == "__main__":
    unittest.main()
