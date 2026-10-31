import tempfile
import unittest
import sys
from types import SimpleNamespace
from unittest.mock import patch
from pathlib import Path

from core.sync_client import SyncClientError, SyncOutbox, SyncHTTPTransport, KeyringCredentialVault, SyncAgent, SyncHTTPError


def make_event(event_id="event_00000001", revision=1):
    return {
        "event_id": event_id, "device_id": "device_0000001",
        "collection": "task_records", "object_id": "task_00000001",
        "revision": revision, "occurred_at": "2026-10-31T10:00:00Z",
        "operation": "upsert", "payload": {"status": "open"},
    }


class FakeSyncVault:
    def load(self):
        return ("device_0000001", "access-token", "refresh-token")
    def save(self, device_id, access, refresh):
        self.saved = (device_id, access, refresh)
    def delete(self):
        self.deleted = True


class FakeUploadTransport:
    def __init__(self, fail_first=None):
        self.push_calls = []
        self.pull_calls = 0
        self.ack_calls = 0
        self.refresh_calls = 0
        self.fail_first = fail_first

    def push(self, event, token):
        self.push_calls.append((event, token))
        if self.fail_first:
            status = self.fail_first
            self.fail_first = None
            raise SyncHTTPError(status, "rejected")

    def refresh(self, token):
        self.refresh_calls += 1
        return {"device_id": "device_0000001", "access_token": "new-access",
                "refresh_token": "new-refresh"}

    def pull(self, *args, **kwargs):
        self.pull_calls += 1
        raise AssertionError("upload-only operation must not pull remote events")

    def acknowledge(self, *args, **kwargs):
        self.ack_calls += 1
        raise AssertionError("upload-only operation must not acknowledge remote events")


class SyncUploadOnlyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.outbox = SyncOutbox(Path(self.tmp.name) / "outbox.sqlite3")
        self.outbox.enqueue(make_event())

    def tearDown(self):
        self.outbox.close()
        self.tmp.cleanup()

    def test_upload_pending_never_pulls_or_applies_remote_events(self):
        transport = FakeUploadTransport()
        applied = []
        agent = SyncAgent(transport, self.outbox, FakeSyncVault(), applied.append)
        result = agent.push_pending()
        self.assertEqual(result, {"pushed": 1, "conflicts": 0, "pending": 0})
        self.assertEqual(len(transport.push_calls), 1)
        self.assertEqual(transport.pull_calls, 0)
        self.assertEqual(transport.ack_calls, 0)
        self.assertEqual(applied, [])
        self.assertEqual(self.outbox.counts()["sent"], 1)

    def test_upload_pending_rotates_credentials_after_unauthorized(self):
        transport = FakeUploadTransport(fail_first=401)
        vault = FakeSyncVault()
        agent = SyncAgent(transport, self.outbox, vault, lambda _event: None)
        result = agent.push_pending()
        self.assertEqual(result["pushed"], 1)
        self.assertEqual(transport.refresh_calls, 1)
        self.assertEqual(transport.push_calls[-1][1], "new-access")

    def test_upload_pending_quarantines_revision_conflict(self):
        transport = FakeUploadTransport(fail_first=409)
        agent = SyncAgent(transport, self.outbox, FakeSyncVault(), lambda _event: None)
        result = agent.push_pending()
        self.assertEqual(result["conflicts"], 1)
        self.assertEqual(self.outbox.counts()["conflict"], 1)
        self.assertEqual(transport.pull_calls, 0)



class FakePageTransport(FakeUploadTransport):
    def __init__(self, events):
        super().__init__()
        self.events = events
        self.pull_calls = []
        self.ack_calls = []

    def pull(self, after, token, limit=100):
        self.pull_calls.append((after, token, limit))
        return {"events": self.events, "next_after": self.events[-1]["sequence"] if self.events else after}

    def acknowledge(self, sequence, token):
        self.ack_calls.append((sequence, token))
        return {}


class SyncReplaySafetyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.outbox = SyncOutbox(Path(self.tmp.name) / "outbox.sqlite3")

    def tearDown(self):
        self.outbox.close()
        self.tmp.cleanup()

    def test_failed_remote_apply_does_not_ack_or_advance_cursor(self):
        event = make_event(event_id="event_remote_0001")
        transport = FakePageTransport([{"sequence": 7, **event}])
        attempts = []
        def fail_first(payload):
            attempts.append(payload)
            raise SyncClientError("simulated local persistence failure")
        agent = SyncAgent(transport, self.outbox, FakeSyncVault(), fail_first)

        with self.assertRaisesRegex(SyncClientError, "simulated local persistence failure"):
            agent.sync_once()
        self.assertEqual(self.outbox.cursor(), 0)
        self.assertEqual(transport.ack_calls, [])
        self.assertEqual(len(attempts), 1)

        applied = []
        agent.apply_remote = applied.append
        result = agent.sync_once()
        self.assertEqual(result["pulled"], 1)
        self.assertEqual(applied[0]["event_id"], event["event_id"])
        self.assertEqual(applied[0]["occurred_at"], "2026-10-31T10:00:00+00:00")
        self.assertEqual(self.outbox.cursor(), 7)
        self.assertEqual(transport.ack_calls, [(7, "access-token")])

    def test_durably_recorded_remote_conflict_is_counted_and_acknowledged(self):
        event = make_event(event_id="event_remote_conflict")
        transport = FakePageTransport([{"sequence": 9, **event}])
        agent = SyncAgent(transport, self.outbox, FakeSyncVault(),
                          lambda _event: "conflict")
        result = agent.sync_once()
        self.assertEqual(result["conflicts"], 1)
        self.assertEqual(result["pulled"], 1)
        self.assertEqual(self.outbox.cursor(), 9)
        self.assertEqual(transport.ack_calls, [(9, "access-token")])

    def test_pull_limit_is_bounded_before_any_network_call(self):
        transport = FakePageTransport([])
        agent = SyncAgent(transport, self.outbox, FakeSyncVault(), lambda _event: None)
        for invalid in (0, 201, True, 1.5, "10"):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    agent.sync_once(pull_limit=invalid)
        self.assertEqual(transport.pull_calls, [])
        self.assertEqual(transport.push_calls, [])



class SyncOutboxTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.outbox = SyncOutbox(Path(self.tmp.name) / "outbox.sqlite3")

    def tearDown(self):
        self.tmp.cleanup()

    def test_enqueue_is_durable_and_idempotent(self):
        self.assertTrue(self.outbox.enqueue(make_event()))
        self.assertFalse(self.outbox.enqueue(make_event()))
        self.assertEqual(self.outbox.ready(), [make_event()])

    def test_ready_preserves_enqueue_order_when_timestamps_match(self):
        first = make_event("event_zzzzzz01")
        second = make_event("event_aaaaaaaa")
        second["object_id"] = "task_00000002"
        self.outbox.enqueue(first)
        self.outbox.enqueue(second)
        self.assertEqual(self.outbox.ready(), [first, second])

    def test_event_id_reuse_with_different_payload_is_rejected(self):
        self.outbox.enqueue(make_event())
        changed = make_event()
        changed["payload"] = {"status": "closed"}
        with self.assertRaises(SyncClientError):
            self.outbox.enqueue(changed)

    def test_retry_uses_bounded_exponential_backoff(self):
        self.outbox.enqueue(make_event())
        self.outbox.mark_retry("event_00000001", "temporary", now=1000)
        self.assertEqual(self.outbox.ready(now=1001), [])
        self.assertEqual(self.outbox.ready(now=1005), [make_event()])

    def test_conflicts_are_quarantined_not_retried(self):
        self.outbox.enqueue(make_event())
        self.outbox.mark_conflict("event_00000001", "revision mismatch")
        self.assertEqual(self.outbox.ready(), [])
        self.assertEqual(self.outbox.counts()["conflict"], 1)

    def test_cursor_is_monotonic(self):
        self.outbox.set_cursor(12)
        self.assertEqual(self.outbox.cursor(), 12)
        with self.assertRaises(ValueError):
            self.outbox.set_cursor(11)

    def test_memory_outbox_survives_separate_connections(self):
        memory = SyncOutbox(":memory:")
        try:
            self.assertTrue(memory.enqueue(make_event()))
            self.assertEqual(memory.ready(), [make_event()])
        finally:
            memory.close()

    def test_keyring_rejects_non_os_backends(self):
        fake = SimpleNamespace(get_keyring=lambda: object())
        with patch.dict(sys.modules, {"keyring": fake}), patch("platform.system", return_value="Windows"):
            with self.assertRaises(SyncClientError):
                KeyringCredentialVault()

    def test_keyring_accepts_windows_vault_backend(self):
        class WinVaultKeyring:
            __module__ = "keyring.backends.Windows"
        fake = SimpleNamespace(get_keyring=lambda: WinVaultKeyring())
        with patch.dict(sys.modules, {"keyring": fake}), patch("platform.system", return_value="Windows"):
            vault = KeyringCredentialVault()
            self.assertIs(vault._keyring, fake)

    def test_transport_rejects_non_https_endpoints(self):
        with self.assertRaises(ValueError):
            SyncHTTPTransport("http://sync.example.com")


if __name__ == "__main__":
    unittest.main()
