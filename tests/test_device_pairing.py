"""Security tests for the local device-pairing store."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from core.device_pairing import DevicePairingStore, PairingError


class DevicePairingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = DevicePairingStore(str(Path(self.temp.name) / "pairing.sqlite"))
        self.challenge = self.store.create_challenge(ttl_seconds=60)

    def tearDown(self):
        self.temp.cleanup()

    def pair(self, scopes=("sync.events.read", "sync.events.write")):
        return self.store.pair_device(
            challenge_id=self.challenge.challenge_id,
            code=self.challenge.code,
            device_id="laptop-01",
            scopes=scopes,
        )

    def test_pairing_code_is_single_use_and_credential_is_not_persisted_plaintext(self):
        device, credential = self.pair()
        self.assertEqual(device.device_id, "laptop-01")
        self.assertNotEqual(credential, "")
        with self.assertRaises(PairingError):
            self.store.pair_device(
                challenge_id=self.challenge.challenge_id,
                code=self.challenge.code,
                device_id="cloud-01",
                scopes=("sync.events.read",),
            )
        raw = Path(self.store.database_path).read_bytes()
        self.assertNotIn(credential.encode(), raw)

    def test_wrong_code_rejected(self):
        with self.assertRaises(PairingError):
            self.store.pair_device(
                challenge_id=self.challenge.challenge_id,
                code="wrong",
                device_id="laptop-01",
                scopes=("sync.events.read",),
            )

    def test_scope_is_required_and_enforced(self):
        _, credential = self.pair(scopes=("sync.events.read",))
        self.assertEqual(
            self.store.authenticate(
                device_id="laptop-01", credential=credential, required_scope="sync.events.read"
            ).device_id,
            "laptop-01",
        )
        with self.assertRaises(PairingError):
            self.store.authenticate(
                device_id="laptop-01", credential=credential, required_scope="sync.events.write"
            )

    def test_bad_credential_rejected(self):
        self.pair()
        with self.assertRaises(PairingError):
            self.store.authenticate(
                device_id="laptop-01", credential="bad", required_scope="sync.events.read"
            )

    def test_revocation_immediately_blocks_authentication(self):
        _, credential = self.pair()
        revoked = self.store.revoke_device("laptop-01")
        self.assertIsNotNone(revoked.revoked_at)
        with self.assertRaises(PairingError):
            self.store.authenticate(
                device_id="laptop-01", credential=credential, required_scope="sync.events.read"
            )
        self.assertEqual(self.store.list_devices(), [])

    def test_only_known_scopes_are_accepted(self):
        with self.assertRaises(PairingError):
            self.store.pair_device(
                challenge_id=self.challenge.challenge_id,
                code=self.challenge.code,
                device_id="laptop-01",
                scopes=("admin.root",),
            )

    def test_audit_does_not_include_pairing_code_or_credential(self):
        _, credential = self.pair()
        audit = repr(self.store.audit_events())
        self.assertNotIn(self.challenge.code, audit)
        self.assertNotIn(credential, audit)

    def test_revocation_is_idempotent(self):
        self.pair()
        first = self.store.revoke_device("laptop-01")
        second = self.store.revoke_device("laptop-01")
        self.assertEqual(first.revoked_at, second.revoked_at)
        self.assertEqual(len([e for e in self.store.audit_events() if e["event"] == "device_revoked"]), 1)


if __name__ == "__main__":
    unittest.main()
