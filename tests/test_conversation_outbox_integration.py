import tempfile
import unittest
from pathlib import Path

from core.conversation_store import ConversationStore
from core.sync_client import SyncOutbox


class ConversationOutboxIntegrationTests(unittest.TestCase):
    def test_chat_export_uses_real_outbox_and_preserves_message_enqueue_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = ConversationStore(root / "history.sqlite3")
            outbox = SyncOutbox(root / "outbox.sqlite3")
            try:
                conversation_id = store.create("Test chat", "2026-10-31T09:00:00Z")
                store.append(conversation_id, "user", "First", "2026-10-31T09:01:00Z")
                store.append(conversation_id, "assistant", "Second", "2026-10-31T09:01:00Z")
                queued = store.queue_sync("device_0000001", outbox.enqueue)
                self.assertEqual(queued, {"conversations": 1, "messages": 2})
                ready = outbox.ready()
                self.assertEqual(
                    [item["collection"] for item in ready],
                    ["conversation_metadata", "conversation_messages", "conversation_messages"],
                )
                self.assertEqual(
                    [item["payload"].get("content") for item in ready[1:]],
                    ["First", "Second"],
                )
            finally:
                outbox.close()
                store.close()


if __name__ == "__main__":
    unittest.main()
