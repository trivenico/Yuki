import tempfile
import unittest
from pathlib import Path

from core.conversation_store import ConversationStore
from core.sync_client import SyncClientError


def event(device="device_0000001", collection="conversation_metadata", object_id="conversation_0000001", payload=None):
    return {
        "event_id": "event_00000001" if collection == "conversation_metadata" else "event_00000002",
        "device_id": device,
        "collection": collection,
        "object_id": object_id,
        "revision": 1,
        "occurred_at": "2026-10-31T09:00:00+04:00",
        "operation": "upsert",
        "payload": payload or {"title": "Remote chat", "created_at": "2026-10-31T09:00:00+04:00"},
    }


class ConversationStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = ConversationStore(Path(self.tmp.name) / "conversations.sqlite3")

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_persists_conversations_and_ordered_messages(self):
        conversation = self.store.create("Build Yuki", "2026-10-31T09:00:00+04:00")
        self.store.append(conversation, "user", "Hello", "2026-10-31T09:01:00+04:00")
        self.store.append(conversation, "assistant", "Hi", "2026-10-31T09:01:01+04:00")
        self.assertEqual([m["role"] for m in self.store.messages(conversation)], ["user", "assistant"])
        self.assertEqual([m["ordinal"] for m in self.store.messages(conversation)], [1, 2])

    def test_preview_is_read_only(self):
        conversation = self.store.create("Build Yuki", "2026-10-31T09:00:00+04:00")
        self.store.append(conversation, "user", "Hello", "2026-10-31T09:01:00+04:00")
        before = self.store.preview_sync()
        self.assertEqual(before, {"conversations": 1, "messages": 1})
        self.assertEqual(self.store.preview_sync(), before)

    def test_queue_is_explicit_and_idempotent_after_metadata_persisted(self):
        conversation = self.store.create("Build Yuki", "2026-10-31T09:00:00+04:00")
        self.store.append(conversation, "user", "Hello", "2026-10-31T09:01:00+04:00")
        events = []
        first = self.store.queue_sync("device_0000001", lambda item: events.append(item) or True)
        second = self.store.queue_sync("device_0000001", lambda item: events.append(item) or True)
        self.assertEqual(first, {"conversations": 1, "messages": 1})
        self.assertEqual(second, {"conversations": 0, "messages": 0})
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["collection"], "conversation_metadata")
        self.assertEqual(events[1]["collection"], "conversation_messages")

    def test_queue_failure_does_not_mark_conversation_as_queued(self):
        self.store.create("Build Yuki", "2026-10-31T09:00:00+04:00")
        with self.assertRaises(SyncClientError):
            self.store.queue_sync("device_0000001", lambda _: (_ for _ in ()).throw(SyncClientError("offline")))
        self.assertEqual(self.store.preview_sync()["conversations"], 1)

    def test_remote_metadata_and_message_apply_idempotently(self):
        metadata = event()
        self.assertEqual(self.store.apply_remote(metadata), "created")
        message = event(collection="conversation_messages", object_id="message_0000001", payload={
            "conversation_id": "conversation_0000001", "role": "user", "content": "Hello",
            "created_at": "2026-10-31T09:01:00+04:00", "ordinal": 1,
        })
        self.assertEqual(self.store.apply_remote(message), "created")
        self.assertEqual(self.store.apply_remote(message), "duplicate")
        self.assertEqual(self.store.messages("conversation_0000001")[0]["content"], "Hello")

    def test_reused_event_id_for_another_object_is_rejected(self):
        self.store.apply_remote(event())
        replay = event()
        replay["object_id"] = "conversation_0000002"
        with self.assertRaises(SyncClientError):
            self.store.apply_remote(replay)

    def test_remote_message_before_metadata_is_rejected(self):
        message = event(collection="conversation_messages", object_id="message_0000001", payload={
            "conversation_id": "conversation_0000001", "role": "user", "content": "Hello",
            "created_at": "2026-10-31T09:01:00+04:00", "ordinal": 1,
        })
        with self.assertRaises(SyncClientError):
            self.store.apply_remote(message)

    def test_duplicate_object_id_with_different_event_is_rejected(self):
        self.store.apply_remote(event())
        conflicting = event()
        conflicting["event_id"] = "event_00000009"
        with self.assertRaises(SyncClientError):
            self.store.apply_remote(conflicting)


if __name__ == "__main__":
    unittest.main()
