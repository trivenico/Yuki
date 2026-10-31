import unittest

from core.sync_client import SyncAgent, SyncOutbox, SyncHTTPError


def event(event_id="event_00000001", device="device_0000001", revision=1):
    return {
        "event_id": event_id, "device_id": device,
        "collection": "task_records", "object_id": "task_00000001",
        "revision": revision, "occurred_at": "2026-10-31T10:00:00Z",
        "operation": "upsert", "payload": {"status": "open"},
    }


class MemoryOutbox:
    def __init__(self):
        self.items = [event()]
        self.state = {}
        self._cursor = 0
    def ready(self, limit):
        return [x for x in self.items if self.state.get(x["event_id"], "pending") == "pending"][:limit]
    def mark_sent(self, event_id):
        self.state[event_id] = "sent"
    def mark_retry(self, event_id, error):
        self.state[event_id] = "pending"
    def mark_conflict(self, event_id, error):
        self.state[event_id] = "conflict"
    def cursor(self):
        return self._cursor
    def set_cursor(self, sequence):
        if sequence < self._cursor:
            raise ValueError()
        self._cursor = sequence


class MemoryVault:
    def __init__(self):
        self.value = ("device_0000001", "access-1", "refresh-1")
    def load(self):
        return self.value
    def save(self, device, access, refresh):
        self.value = (device, access, refresh)
    def delete(self):
        self.value = None


class FakeTransport:
    def __init__(self):
        self.pushed = []
        self.acked = []
        self.refreshed = 0
        self.pull_page = {"events": [], "next_after": 0}
        self.expire_once = False
    def push(self, item, access):
        if self.expire_once:
            self.expire_once = False
            raise SyncHTTPError(401, "expired")
        self.pushed.append(item["event_id"])
        return {"sequence": len(self.pushed)}
    def refresh(self, refresh):
        self.refreshed += 1
        return {"device_id": "device_0000001", "access_token": "access-2", "refresh_token": "refresh-2"}
    def pull(self, cursor, access, limit):
        return self.pull_page
    def acknowledge(self, sequence, access):
        self.acked.append(sequence)


class SyncAgentTests(unittest.TestCase):
    def test_sync_cycle_pushes_and_applies_remote_before_ack(self):
        outbox, vault, transport = MemoryOutbox(), MemoryVault(), FakeTransport()
        remote = event("event_00000002", "device_0000002", 1)
        transport.pull_page = {"events": [{"sequence": 3, **remote}], "next_after": 3}
        applied = []
        agent = SyncAgent(transport, outbox, vault, applied.append)
        result = agent.sync_once()
        self.assertEqual(result, {"pushed": 1, "pulled": 1, "conflicts": 0})
        self.assertEqual(transport.acked, [3])
        self.assertEqual(outbox.cursor(), 3)
        self.assertEqual(applied[0]["event_id"], "event_00000002")

    def test_expired_access_token_is_refreshed_and_saved(self):
        outbox, vault, transport = MemoryOutbox(), MemoryVault(), FakeTransport()
        transport.expire_once = True
        agent = SyncAgent(transport, outbox, vault, lambda event: None)
        agent.sync_once()
        self.assertEqual(transport.refreshed, 1)
        self.assertEqual(vault.value[1:], ("access-2", "refresh-2"))
        self.assertEqual(outbox.state["event_00000001"], "sent")

    def test_remote_event_is_validated_before_application(self):
        outbox, vault, transport = MemoryOutbox(), MemoryVault(), FakeTransport()
        bad = event("event_00000002", "device_0000002")
        bad["payload"] = {"api_token": "do-not-sync"}
        transport.pull_page = {"events": [{"sequence": 1, **bad}], "next_after": 1}
        applied = []
        agent = SyncAgent(transport, outbox, vault, applied.append)
        with self.assertRaises(ValueError):
            agent.sync_once()
        self.assertEqual(applied, [])
        self.assertEqual(transport.acked, [])

    def test_conflicting_push_is_quarantined(self):
        class ConflictTransport(FakeTransport):
            def push(self, item, access):
                raise SyncHTTPError(409, "conflict")
        outbox, vault, transport = MemoryOutbox(), MemoryVault(), ConflictTransport()
        agent = SyncAgent(transport, outbox, vault, lambda event: None)
        result = agent.sync_once()
        self.assertEqual(result["conflicts"], 1)
        self.assertEqual(outbox.state["event_00000001"], "conflict")


if __name__ == "__main__":
    unittest.main()
