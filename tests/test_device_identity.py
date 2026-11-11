import base64
import tempfile
import unittest
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from core.device_identity import DeviceIdentityError, DeviceIdentityRegistry
from core.device_pairing import DevicePairingService


def _b64(value):
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


class DeviceIdentityRegistryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.database = str(Path(self.temp.name) / "devices.sqlite3")
        self.pairing = DevicePairingService(self.database)
        challenge = self.pairing.create_pairing_challenge("owner")
        self.credential = self.pairing.pair_device(challenge.code, "Phone")
        self.registry = DeviceIdentityRegistry(self.pairing)

    def tearDown(self):
        self.temp.cleanup()

    def _key(self):
        private = Ed25519PrivateKey.generate()
        public = private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        return private, _b64(public)

    def _challenge(self, *, owner="owner", device_id=None, public_key=None, confirmed=True):
        _, generated = self._key() if public_key is None else (None, public_key)
        return self.registry.create_challenge(
            owner, device_id or self.credential.device.device_id, generated,
            owner_confirmed=confirmed,
        )

    def _activate(self, private, challenge):
        transcript = self.registry._transcript(
            challenge.challenge_id, challenge.nonce, "owner",
            self.credential.device.device_id, challenge.key_fingerprint,
            challenge.generation, challenge.expires_at,
        )
        signature = _b64(private.sign(transcript))
        return self.registry.activate_key(
            self.credential.token, challenge.challenge_id, challenge.nonce, signature
        )

    def test_owner_approval_and_device_ownership_are_required(self):
        _, public = self._key()
        with self.assertRaises(DeviceIdentityError):
            self.registry.create_challenge(
                "owner", self.credential.device.device_id, public, owner_confirmed=False
            )
        with self.assertRaises(DeviceIdentityError):
            self.registry.create_challenge(
                "another-owner", self.credential.device.device_id, public, owner_confirmed=True
            )

    def test_enrollment_requires_proof_of_private_key_possession(self):
        private, _ = self._key()
        challenge = self._challenge()
        other = Ed25519PrivateKey.generate()
        transcript = self.registry._transcript(
            challenge.challenge_id, challenge.nonce, "owner",
            self.credential.device.device_id, challenge.key_fingerprint,
            challenge.generation, challenge.expires_at,
        )
        with self.assertRaises(DeviceIdentityError):
            self.registry.activate_key(
                self.credential.token, challenge.challenge_id, challenge.nonce,
                _b64(other.sign(transcript)),
            )
        self.assertIsNone(self.registry.get_active_key("owner", self.credential.device.device_id))
        self.assertEqual(self._activate(private, challenge).status, "active")

    def test_challenge_is_single_use_and_bound_to_device_and_nonce(self):
        private, _ = self._key()
        challenge = self._challenge()
        transcript = self.registry._transcript(
            challenge.challenge_id, challenge.nonce, "owner",
            self.credential.device.device_id, challenge.key_fingerprint,
            challenge.generation, challenge.expires_at,
        )
        signature = _b64(private.sign(transcript))
        with self.assertRaises(DeviceIdentityError):
            self.registry.activate_key(
                self.credential.token, challenge.challenge_id, "A" * 43, signature
            )
        activated = self.registry.activate_key(
            self.credential.token, challenge.challenge_id, challenge.nonce, signature
        )
        self.assertEqual(activated.generation, 1)
        with self.assertRaises(DeviceIdentityError):
            self.registry.activate_key(
                self.credential.token, challenge.challenge_id, challenge.nonce, signature
            )

    def test_existing_active_key_cannot_be_overwritten_by_new_enrollment(self):
        private, _ = self._key()
        challenge = self._challenge()
        first = self._activate(private, challenge)
        _, next_public = self._key()
        with self.assertRaises(DeviceIdentityError):
            self.registry.create_challenge(
                "owner", self.credential.device.device_id, next_public, owner_confirmed=True
            )
        self.assertEqual(
            self.registry.get_active_key("owner", self.credential.device.device_id).key_id,
            first.key_id,
        )

    def test_revocation_atomically_invalidates_active_key(self):
        private, _ = self._key()
        challenge = self._challenge()
        self._activate(private, challenge)
        self.assertTrue(self.pairing.revoke_device("owner", self.credential.device.device_id))
        self.assertIsNone(self.registry.get_active_key("owner", self.credential.device.device_id))
        with self.assertRaises(DeviceIdentityError):
            self._challenge()

    def test_device_bearer_must_match_challenge_device(self):
        private, _ = self._key()
        challenge = self._challenge()
        second_challenge = self.pairing.create_pairing_challenge("owner")
        second_device = self.pairing.pair_device(second_challenge.code, "Tablet")
        transcript = self.registry._transcript(
            challenge.challenge_id, challenge.nonce, "owner",
            self.credential.device.device_id, challenge.key_fingerprint,
            challenge.generation, challenge.expires_at,
        )
        with self.assertRaises(DeviceIdentityError):
            self.registry.activate_key(
                second_device.token, challenge.challenge_id, challenge.nonce,
                _b64(private.sign(transcript)),
            )

    def test_public_key_metadata_is_owner_scoped_and_contains_no_private_key(self):
        private, _ = self._key()
        challenge = self._challenge()
        activated = self._activate(private, challenge)
        self.assertEqual(activated.algorithm, "ed25519")
        self.assertEqual(
            self.registry.get_active_key("owner", self.credential.device.device_id),
            activated,
        )
        self.assertIsNone(
            self.registry.get_active_key("other-owner", self.credential.device.device_id)
        )
        self.assertNotIn("private", repr(activated).lower())


if __name__ == "__main__":
    unittest.main()
