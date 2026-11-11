import tempfile
import unittest
from pathlib import Path

from core.device_pairing import DevicePairingService
from core.lan_devices import (
    DeviceCapability,
    LocalDeviceDenied,
    LocalDeviceError,
    LocalNetworkDeviceRegistry,
)


class LocalNetworkDeviceRegistryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.database = str(Path(self.temp.name) / "devices.sqlite3")
        self.pairing = DevicePairingService(self.database)
        challenge = self.pairing.create_pairing_challenge("owner")
        self.credential = self.pairing.pair_device(challenge.code, "Phone")
        self.registry = LocalNetworkDeviceRegistry(self.pairing)

    def tearDown(self):
        self.temp.cleanup()

    def register(self, **overrides):
        values = {
            "owner_id": "owner",
            "device_id": self.credential.device.device_id,
            "endpoint": "http://192.168.1.25:8765",
            "capabilities": [DeviceCapability.STATUS.value, DeviceCapability.NOTIFY.value,
                             DeviceCapability.CALL_START.value, DeviceCapability.CALL_ACCEPT.value,
                             DeviceCapability.DEVICE_LOCK.value],
            "owner_confirmed": True,
        }
        values.update(overrides)
        return self.registry.register_endpoint(**values)

    def test_only_paired_owner_can_register_endpoint(self):
        with self.assertRaises(LocalDeviceDenied):
            self.registry.register_endpoint(
                owner_id="other", device_id=self.credential.device.device_id,
                endpoint="http://192.168.1.25:8765", capabilities=[],
                owner_confirmed=True,
            )
        with self.assertRaises(LocalDeviceDenied):
            self.register(owner_confirmed=1)

    def test_endpoint_requires_owner_confirmation_and_literal_ip(self):
        with self.assertRaises(LocalDeviceDenied):
            self.register(owner_confirmed=False)
        for endpoint in (
            "http://example.com:8765", "http://8.8.8.8:8765",
            "http://127.0.0.1:8765", "http://169.254.1.2:8765",
            "http://192.168.1.25:8765/path", "http://user:pass@192.168.1.25",
            "https://192.168.1.25?token=secret",
        ):
            with self.subTest(endpoint=endpoint):
                with self.assertRaises(LocalDeviceError):
                    self.register(endpoint=endpoint)

    def test_supported_capabilities_only_and_revision_compare_and_swap(self):
        first = self.register()
        self.assertEqual(first.revision, 1)
        self.assertIn(DeviceCapability.STATUS.value, first.capabilities)
        with self.assertRaises(LocalDeviceError):
            self.register(capabilities=["shell.exec"])
        second = self.register(expected_revision=1, capabilities=[DeviceCapability.STATUS.value])
        self.assertEqual(second.revision, 2)
        with self.assertRaises(LocalDeviceError):
            self.register(expected_revision=1)

    def test_owner_confirmation_required_for_lock_and_call(self):
        self.register()
        with self.assertRaises(LocalDeviceDenied):
            self.registry.authorize(
                owner_id="owner", device_id=self.credential.device.device_id,
                capability=DeviceCapability.DEVICE_LOCK,
            )
        with self.assertRaises(LocalDeviceDenied):
            self.registry.authorize(
                owner_id="owner", device_id=self.credential.device.device_id,
                capability=DeviceCapability.CALL_START, owner_confirmed=True,
            )
        grant = self.registry.authorize(
            owner_id="owner", device_id=self.credential.device.device_id,
            capability=DeviceCapability.CALL_START, owner_confirmed=True,
            recipient_accepted=True,
        )
        self.assertEqual(grant.capability, DeviceCapability.CALL_START)
        self.assertTrue(grant.recipient_accepted)

    def test_ungranted_capability_and_unknown_command_are_denied(self):
        self.register(capabilities=[DeviceCapability.STATUS.value])
        with self.assertRaises(LocalDeviceDenied):
            self.registry.authorize(
                owner_id="owner", device_id=self.credential.device.device_id,
                capability=DeviceCapability.DEVICE_LOCK, owner_confirmed=True,
            )
        with self.assertRaises(LocalDeviceDenied):
            self.registry.authorize(
                owner_id="owner", device_id=self.credential.device.device_id,
                capability="shell.exec", owner_confirmed=True,
            )

    def test_revoked_device_cannot_be_authorized_or_listed(self):
        self.register()
        self.assertTrue(self.pairing.revoke_device("owner", self.credential.device.device_id))
        self.assertEqual(self.registry.list_devices(owner_id="owner"), ())
        with self.assertRaises(LocalDeviceDenied):
            self.registry.authorize(
                owner_id="owner", device_id=self.credential.device.device_id,
                capability=DeviceCapability.STATUS,
            )

    def test_endpoint_revision_and_owner_scope_are_enforced(self):
        self.register()
        with self.assertRaises(LocalDeviceDenied):
            self.registry.authorize(
                owner_id="other", device_id=self.credential.device.device_id,
                capability=DeviceCapability.STATUS,
            )
        listed = self.registry.list_devices(owner_id="owner")
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0].endpoint, "http://192.168.1.25:8765")


if __name__ == "__main__":
    unittest.main()
