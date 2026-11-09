import tempfile
import threading
import unittest
from pathlib import Path

from core.device_pairing import DevicePairingError, DevicePairingService


class DevicePairingServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.service = DevicePairingService(Path(self.tmp.name) / "devices.sqlite")

    def test_pairing_is_owner_bound_and_token_is_not_persisted(self):
        challenge = self.service.create_pairing_challenge("owner-1")
        credential = self.service.pair_device(challenge.code, "Yuki Mini PC")
        self.assertEqual(credential.device.owner_id, "owner-1")
        self.assertEqual(credential.device.name, "Yuki Mini PC")
        self.assertEqual(self.service.authenticate_device(credential.token).device_id,
                         credential.device.device_id)
        import sqlite3
        db = sqlite3.connect(self.service.database_path)
        try:
            stored = db.execute("SELECT token_hash FROM paired_devices").fetchone()[0]
        finally:
            db.close()
        self.assertNotEqual(stored, credential.token)

    def test_pairing_challenge_is_single_use(self):
        challenge = self.service.create_pairing_challenge("owner-1")
        self.service.pair_device(challenge.code, "Desk Node")
        with self.assertRaises(DevicePairingError):
            self.service.pair_device(challenge.code, "Second Node")

    def test_invalid_challenge_and_name_are_rejected(self):
        with self.assertRaises(DevicePairingError):
            self.service.pair_device("not-a-real-challenge-token", "Desk")
        challenge = self.service.create_pairing_challenge("owner-1")
        with self.assertRaises(ValueError):
            self.service.pair_device(challenge.code, "bad\nname")

    def test_expired_challenge_is_rejected(self):
        from unittest.mock import patch
        from datetime import datetime, timedelta, timezone
        challenge = self.service.create_pairing_challenge("owner-1")
        expired = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        with self.service._connect() as db:
            db.execute("UPDATE device_pairing_challenges SET expires_at=?",
                       (expired,))
        with self.assertRaises(DevicePairingError):
            self.service.pair_device(challenge.code, "Desk")

    def test_revoke_is_owner_scoped_and_immediate(self):
        challenge = self.service.create_pairing_challenge("owner-1")
        credential = self.service.pair_device(challenge.code, "Desk")
        self.assertFalse(self.service.revoke_device("owner-2", credential.device.device_id))
        self.assertTrue(self.service.revoke_device("owner-1", credential.device.device_id))
        self.assertIsNone(self.service.authenticate_device(credential.token))

    def test_exact_active_device_ownership_check(self):
        credential = self.service.pair_device(
            self.service.create_pairing_challenge("owner-1").code, "Node A"
        )
        self.assertTrue(self.service.owns_active_device(
            "owner-1", credential.device.device_id
        ))
        self.assertFalse(self.service.owns_active_device(
            "owner-2", credential.device.device_id
        ))
        self.assertFalse(self.service.owns_active_device(
            "owner-1", "missing-device"
        ))

    def test_exact_active_device_ownership_rejects_revoked_device(self):
        credential = self.service.pair_device(
            self.service.create_pairing_challenge("owner-1").code, "Node A"
        )
        self.assertTrue(self.service.revoke_device(
            "owner-1", credential.device.device_id
        ))
        self.assertFalse(self.service.owns_active_device(
            "owner-1", credential.device.device_id
        ))

    def test_device_listing_is_owner_scoped(self):
        first = self.service.pair_device(
            self.service.create_pairing_challenge("owner-1").code, "Node A"
        )
        self.service.pair_device(
            self.service.create_pairing_challenge("owner-2").code, "Node B"
        )
        self.assertEqual([d.device_id for d in self.service.list_devices("owner-1")],
                         [first.device.device_id])

    def test_concurrent_replay_only_allows_one_device(self):
        challenge = self.service.create_pairing_challenge("owner-1")
        outcomes = []
        lock = threading.Lock()
        def attempt(name):
            try:
                result = self.service.pair_device(challenge.code, name)
                value = ("ok", result.device.device_id)
            except DevicePairingError:
                value = ("rejected", None)
            with lock:
                outcomes.append(value)
        threads = [threading.Thread(target=attempt, args=(f"Node {i}",)) for i in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(sum(status == "ok" for status, _ in outcomes), 1)
        self.assertEqual(sum(status == "rejected" for status, _ in outcomes), 1)

    def test_invalid_configuration_is_rejected(self):
        with self.assertRaises(ValueError):
            DevicePairingService(Path(self.tmp.name) / "bad.sqlite", pairing_ttl_seconds=10)


if __name__ == "__main__":
    unittest.main()
