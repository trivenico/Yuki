import unittest

from core.oauth_aead import AESGCMCredentialCipher, OAuthKeyUnavailable


class RotatingKeyProvider:
    def __init__(self):
        self.keys = {"key-v1": b"1" * 32}
        self.active = "key-v1"

    def current_key(self):
        return self.active, self.keys[self.active]

    def get_key(self, key_id):
        return self.keys[key_id]

    def rotate(self, key_id, key):
        self.keys[key_id] = key
        self.active = key_id


class AESGCMCredentialCipherTests(unittest.TestCase):
    def setUp(self):
        self.keys = RotatingKeyProvider()
        self.cipher = AESGCMCredentialCipher(self.keys)
        self.aad = b'[1,"owner","provider","account","access"]'

    def test_round_trip_and_randomized_nonce(self):
        key_id, first = self.cipher.encrypt(b"secret-token", associated_data=self.aad)
        _, second = self.cipher.encrypt(b"secret-token", associated_data=self.aad)
        self.assertEqual(key_id, "key-v1")
        self.assertNotEqual(first, second)
        self.assertEqual(self.cipher.decrypt(key_id, first, associated_data=self.aad), b"secret-token")

    def test_authentication_rejects_modified_ciphertext(self):
        key_id, ciphertext = self.cipher.encrypt(b"secret-token", associated_data=self.aad)
        altered = ciphertext[:-1] + bytes([ciphertext[-1] ^ 1])
        with self.assertRaises(OAuthKeyUnavailable):
            self.cipher.decrypt(key_id, altered, associated_data=self.aad)

    def test_authentication_binds_associated_data(self):
        key_id, ciphertext = self.cipher.encrypt(b"secret-token", associated_data=self.aad)
        with self.assertRaises(OAuthKeyUnavailable):
            self.cipher.decrypt(key_id, ciphertext, associated_data=b"other-owner")

    def test_old_key_remains_decryptable_after_rotation(self):
        key_id, ciphertext = self.cipher.encrypt(b"secret-token", associated_data=self.aad)
        self.keys.rotate("key-v2", b"2" * 32)
        next_id, next_ciphertext = self.cipher.encrypt(b"new-token", associated_data=self.aad)
        self.assertEqual(next_id, "key-v2")
        self.assertEqual(self.cipher.decrypt(key_id, ciphertext, associated_data=self.aad), b"secret-token")
        self.assertEqual(self.cipher.decrypt(next_id, next_ciphertext, associated_data=self.aad), b"new-token")

    def test_missing_historical_key_fails_closed(self):
        key_id, ciphertext = self.cipher.encrypt(b"secret-token", associated_data=self.aad)
        del self.keys.keys[key_id]
        with self.assertRaises(OAuthKeyUnavailable):
            self.cipher.decrypt(key_id, ciphertext, associated_data=self.aad)

    def test_invalid_key_material_rejected(self):
        self.keys.keys["bad"] = b"short"
        self.keys.active = "bad"
        with self.assertRaises(OAuthKeyUnavailable):
            self.cipher.encrypt(b"secret-token", associated_data=self.aad)

    def test_bounds_and_argument_types(self):
        with self.assertRaises(ValueError):
            self.cipher.encrypt(b"", associated_data=self.aad)
        with self.assertRaises(ValueError):
            self.cipher.encrypt(b"secret", associated_data=b"")
        with self.assertRaises(ValueError):
            self.cipher.decrypt("key-v1", b"short", associated_data=self.aad)


if __name__ == "__main__":
    unittest.main()
