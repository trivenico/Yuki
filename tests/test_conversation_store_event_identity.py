import tempfile
import unittest
from pathlib import Path

from core.conversation_store import ConversationStore
from core.sync_client import SyncClientError


class ConversationEventIdentityTests(unittest.TestCase):
    def test_reused_event_id_for_another_object_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ConversationStore(Path(tmp) / "conversations.sqlite3")
            try:
                first = {
                    "event_id": "event_00000001", "device_id": "device_0000001",
                    "collection": "conversation_metadata", "object_id": "conversation_0000001",
                    "revision": 1, "occurred_at": "2026-10-31T09:00:00+04:00",
                    "operation": "upsert",
                    "payload": {"title": "First", "created_at": "2026-10-31T09:00:00+04:00"},
                }
                store.apply_remote(first)
                replay = dict(first)
                replay["object_id"] = "conversation_0000002"
                with self.assertRaises(SyncClientError):
                    store.apply_remote(replay)
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
