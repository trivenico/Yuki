import tempfile
import unittest
from pathlib import Path

from core.conversation_store import ConversationStore


class ConversationOrderingTests(unittest.TestCase):
    def test_remote_messages_with_same_origin_ordinal_get_local_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = ConversationStore(Path(tmp) / "history.sqlite3")
            try:
                store.apply_remote({
                    "event_id": "event_metadata01", "device_id": "device_0000001",
                    "collection": "conversation_metadata", "object_id": "conversation_0000001",
                    "revision": 1, "occurred_at": "2026-10-31T09:00:00Z",
                    "operation": "upsert",
                    "payload": {"title": "Shared", "created_at": "2026-10-31T09:00:00Z"},
                })
                for suffix, content in (("01", "From Windows"), ("02", "From Linux")):
                    store.apply_remote({
                        "event_id": "event_message" + suffix, "device_id": "device_0000001",
                        "collection": "conversation_messages", "object_id": "message_" + suffix + "000000",
                        "revision": 1, "occurred_at": "2026-10-31T09:01:00Z",
                        "operation": "upsert",
                        "payload": {
                            "conversation_id": "conversation_0000001", "role": "user",
                            "content": content, "created_at": "2026-10-31T09:01:00Z", "ordinal": 1,
                        },
                    })
                rows = store.messages("conversation_0000001")
                self.assertEqual([row["ordinal"] for row in rows], [1, 2])
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
