import tempfile
import unittest
from pathlib import Path

from core.sync_runtime import SyncRuntime
from core.sync_client import SyncClientError


class FakeVault:
    def __init__(self):
        self.value = None
    def load(self):
        return self.value
    def save(self, device_id, access, refresh):
        self.value = (device_id, access, refresh)


class FakeTransport:
    def __init__(self):
        self.pair_calls = 0
    def pair(self, code, device_label):
        self.pair_calls += 1
        return {
            "device_id": "device_0000001",
            "access_token": "a" * 40,
            "refresh_token": "r" * 40,
        }


class SyncRuntimeTests(unittest.TestCase):
    def test_status_is_available_before_pairing(self):
        with tempfile.TemporaryDirectory() as tmp:
            runtime = SyncRuntime(endpoint="", root=tmp, vault=FakeVault(), transport=FakeTransport())
            try:
                status = runtime.status()
                self.assertIn("Device: not paired", status)
                self.assertIn("0 pending", status)
            finally:
                runtime.close()

    def test_pair_saves_credentials_without_returning_tokens(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = FakeVault()
            runtime = SyncRuntime(endpoint="https://sync.example.test", root=tmp,
                                   vault=vault, transport=FakeTransport())
            try:
                message = runtime.pair("one-time-code")
                self.assertEqual(vault.value[0], "device_0000001")
                self.assertNotIn("a" * 40, message)
                self.assertNotIn("r" * 40, message)
            finally:
                runtime.close()

    def test_pair_does_not_consume_code_without_credential_vault(self):
        with tempfile.TemporaryDirectory() as tmp:
            transport = FakeTransport()
            runtime = SyncRuntime(endpoint="https://sync.example.test", root=tmp,
                                  vault=None, transport=transport)
            runtime.vault = None
            try:
                with self.assertRaises(SyncClientError):
                    runtime.pair("one-time-code")
                self.assertEqual(transport.pair_calls, 0)
            finally:
                runtime.close()

    def test_event_enqueue_requires_matching_paired_device(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = FakeVault()
            vault.save("device_0000001", "a" * 40, "r" * 40)
            runtime = SyncRuntime(endpoint="", root=tmp, vault=vault, transport=FakeTransport())
            event = {
                "event_id": "event_00000001", "device_id": "device_0000002",
                "collection": "task_records", "object_id": "task_00000001",
                "revision": 1, "occurred_at": "2026-10-31T10:00:00Z",
                "operation": "upsert", "payload": {"status": "open"},
            }
            try:
                with self.assertRaises(SyncClientError):
                    runtime.enqueue_event(event)
            finally:
                runtime.close()


if __name__ == "__main__":
    unittest.main()
