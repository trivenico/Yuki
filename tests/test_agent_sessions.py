import tempfile
import unittest
from pathlib import Path
from automation.agent_sessions import AgentSessionStore, SessionConflict

class AgentSessionStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = AgentSessionStore(Path(self.tmp.name) / "sessions.sqlite3",
            authorize=lambda actor, action, resource: actor == "alice")

    def create(self, key="create-1"):
        return self.store.create(actor_id="alice", idempotency_key=key,
            title="Project Alpha", agent_id="engineering", model_id="local-model", workspace_id="repo-1")

    def test_list_sessions_is_actor_scoped_and_cursor_paginated(self):
        first = self.create("create-1")
        second = self.store.create(actor_id="alice", idempotency_key="create-2",
            title="Project Beta", agent_id="general")
        page_one = self.store.list_sessions(actor_id="alice", limit=1)
        self.assertEqual(len(page_one), 1)
        cursor = (page_one[-1].updated_at, page_one[-1].session_id)
        page_two = self.store.list_sessions(actor_id="alice", before=cursor, limit=1)
        self.assertEqual(len(page_two), 1)
        self.assertNotEqual(page_one[0].session_id, page_two[0].session_id)
        self.assertEqual({page_one[0].session_id, page_two[0].session_id},
                         {first.session_id, second.session_id})
        with self.assertRaises(PermissionError):
            self.store.list_sessions(actor_id="mallory")

    def test_list_sessions_rejects_invalid_page_size(self):
        with self.assertRaises(ValueError):
            self.store.list_sessions(actor_id="alice", limit=0)

    def test_create_is_idempotent(self):
        first, second = self.create(), self.create()
        self.assertEqual(first.session_id, second.session_id)
        self.assertEqual(first.last_sequence, 1)

    def test_create_key_payload_mismatch_conflicts(self):
        self.create()
        with self.assertRaises(SessionConflict):
            self.store.create(actor_id="alice", idempotency_key="create-1", title="Different title")

    def test_append_and_replay_from_cursor(self):
        session = self.create()
        first = self.store.append(actor_id="alice", session_id=session.session_id,
            idempotency_key="msg-1", kind="message.user", payload={"text": "Build the project"})
        self.store.append(actor_id="alice", session_id=session.session_id,
            idempotency_key="tool-1", kind="tool.completed", payload={"tool": "tests", "status": "passed"})
        replay = self.store.list_events(actor_id="alice", session_id=session.session_id,
            after_sequence=first.sequence, limit=10)
        self.assertEqual([event.kind for event in replay], ["tool.completed"])

    def test_append_is_idempotent_and_detects_payload_mismatch(self):
        session = self.create()
        args = dict(actor_id="alice", session_id=session.session_id, idempotency_key="msg-1",
            kind="message.user", payload={"text": "hello"})
        first, second = self.store.append(**args), self.store.append(**args)
        self.assertEqual(first.event_id, second.event_id)
        with self.assertRaises(SessionConflict):
            self.store.append(**{**args, "payload": {"text": "changed"}})

    def test_actor_isolation(self):
        session = self.create()
        with self.assertRaises(PermissionError):
            self.store.get(actor_id="mallory", session_id=session.session_id)

    def test_stale_version_rejected(self):
        session = self.create()
        self.store.append(actor_id="alice", session_id=session.session_id,
            idempotency_key="msg-1", kind="message.user", payload={"text": "hi"},
            expected_version=session.version)
        with self.assertRaises(SessionConflict):
            self.store.append(actor_id="alice", session_id=session.session_id,
                idempotency_key="msg-2", kind="message.user", payload={"text": "again"},
                expected_version=session.version)

    def test_terminal_sessions_reject_appends(self):
        session = self.create()
        done = self.store.set_state(actor_id="alice", session_id=session.session_id,
            state="completed", expected_version=session.version)
        with self.assertRaises(SessionConflict):
            self.store.append(actor_id="alice", session_id=session.session_id,
                idempotency_key="late", kind="message.user", payload={"text": "late"})
        self.assertEqual(done.state, "completed")

    def test_event_size_is_bounded(self):
        session = self.create()
        with self.assertRaises(ValueError):
            self.store.append(actor_id="alice", session_id=session.session_id,
                idempotency_key="large", kind="message.user", payload={"text": "x" * 70000})

if __name__ == "__main__":
    unittest.main()
