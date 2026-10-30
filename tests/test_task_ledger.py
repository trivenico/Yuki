import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from pathlib import Path

from core.task_ledger import TASK_STATUSES, TaskLedger
from core.task_contracts import TaskEvent


class TaskLedgerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "tasks.sqlite3"
        self.ledger = TaskLedger(self.path)

    def test_create_and_read_task(self):
        task = self.ledger.create("Prepare project report", detail="Collect status", metadata={"source": "desktop"})
        self.assertEqual(task["status"], "queued")
        self.assertEqual(task["title"], "Prepare project report")
        self.assertEqual(task["metadata"], {"source": "desktop"})
        self.assertEqual(self.ledger.get(task["id"])["id"], task["id"])

    def test_valid_lifecycle_and_terminal_state(self):
        task = self.ledger.create("Run tests")
        self.assertEqual(self.ledger.transition(task["id"], "running")["status"], "running")
        self.assertEqual(self.ledger.transition(task["id"], "awaiting_approval")["status"], "awaiting_approval")
        self.assertEqual(self.ledger.transition(task["id"], "running")["status"], "running")
        awaiting = self.ledger.transition(task["id"], "awaiting_verification", result="Runner returned success")
        self.assertEqual(awaiting["status"], "awaiting_verification")
        self.assertNotIn(awaiting["status"], {"completed", "failed", "cancelled"})
        with self.assertRaisesRegex(ValueError, "verification evidence"):
            self.ledger.transition(task["id"], "completed", result="Model says it worked")
        completed = self.ledger.transition(
            task["id"], "completed", result="Independent acceptance check passed",
            verification_evidence="Expected output matched the test assertion.",
        )
        self.assertEqual(completed["result"], "Independent acceptance check passed")
        with self.assertRaisesRegex(ValueError, "transition"):
            self.ledger.transition(task["id"], "running")

    def test_artifact_observation_does_not_complete_task(self):
        task = self.ledger.create("Create a report")
        self.ledger.transition(task["id"], "running")
        self.ledger.transition(task["id"], "awaiting_verification", result="Model reported success")
        updated = self.ledger.record_verification_evidence(
            task["id"], "report.md exists; SHA-256=abc; presence only"
        )
        self.assertEqual(updated["status"], "awaiting_verification")
        self.assertIn("presence only", updated["verification_evidence"])
        with self.assertRaisesRegex(ValueError, "verification evidence"):
            self.ledger.transition(task["id"], "completed", result="done")

    def test_evidence_recording_is_limited_to_awaiting_verification(self):
        task = self.ledger.create("Not ready")
        with self.assertRaisesRegex(ValueError, "awaiting verification"):
            self.ledger.record_verification_evidence(task["id"], "some evidence")
        with self.assertRaisesRegex(ValueError, "non-empty"):
            self.ledger.record_verification_evidence(task["id"], " ")

    def test_failure_and_cancel_are_terminal(self):
        failed = self.ledger.create("Fail task")
        self.ledger.transition(failed["id"], "failed", error_code="provider_error")
        with self.assertRaisesRegex(ValueError, "transition"):
            self.ledger.transition(failed["id"], "completed", verification_evidence="must still be blocked by terminal status")
        cancelled = self.ledger.create("Cancel task")
        self.assertEqual(self.ledger.cancel(cancelled["id"])["status"], "cancelled")
        with self.assertRaisesRegex(ValueError, "transition"):
            self.ledger.cancel(cancelled["id"])

    def test_competing_terminal_transitions_are_serialized(self):
        task = self.ledger.create("Competing updates")
        second_ledger = TaskLedger(self.path)
        barrier = Barrier(2)

        def attempt(ledger, status):
            barrier.wait(timeout=2)
            try:
                return ledger.transition(task["id"], status)["status"]
            except ValueError as exc:
                return type(exc)

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(attempt, self.ledger, "failed"),
                pool.submit(attempt, second_ledger, "cancelled"),
            ]
            outcomes = [future.result(timeout=5) for future in futures]

        self.assertEqual(sum(isinstance(item, str) for item in outcomes), 1)
        self.assertEqual(sum(item is ValueError for item in outcomes), 1)
        self.assertIn(self.ledger.get(task["id"])["status"], {"failed", "cancelled"})

    def test_tasks_survive_reopening_store(self):
        task = self.ledger.create("Persistent task")
        reopened = TaskLedger(self.path)
        self.assertEqual(reopened.get(task["id"])["title"], "Persistent task")

    def test_listing_filters_terminal_tasks_and_respects_limit(self):
        active = self.ledger.create("Active")
        done = self.ledger.create("Done")
        self.ledger.transition(done["id"], "cancelled")
        self.assertEqual([item["id"] for item in self.ledger.list(include_terminal=False)], [active["id"]])
        self.assertEqual(len(self.ledger.list(limit=1)), 1)
        with self.assertRaisesRegex(ValueError, "between 1 and 200"):
            self.ledger.list(limit=0)

    def test_rejects_invalid_input_and_unknown_ids(self):
        with self.assertRaisesRegex(ValueError, "non-empty"):
            self.ledger.create(" ")
        with self.assertRaisesRegex(ValueError, "Unknown"):
            self.ledger.transition("x", "invisible")
        with self.assertRaisesRegex(KeyError, "not found"):
            self.ledger.transition("missing", "running")
        with self.assertRaisesRegex(ValueError, "JSON-serializable"):
            self.ledger.create("Bad metadata", metadata={"value": object()})


    def test_existing_database_schema_is_migrated(self):
        from contextlib import closing
        import sqlite3
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("DROP TABLE tasks")
            db.execute("""
                CREATE TABLE tasks (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    detail TEXT NOT NULL DEFAULT '',
                    result TEXT NOT NULL DEFAULT '',
                    error_code TEXT NOT NULL DEFAULT '',
                    metadata_json TEXT NOT NULL DEFAULT '{}'
                )
            """)
            db.commit()
        reopened = TaskLedger(self.path)
        task = reopened.create("After migration")
        self.assertEqual(task["verification_evidence"], "")
        with closing(sqlite3.connect(self.path)) as db:
            columns = {row[1] for row in db.execute("PRAGMA table_info(tasks)")}
        self.assertIn("verification_evidence", columns)

    def test_task_events_persist_across_reopen_and_are_chronological(self):
        task = self.ledger.create("Timeline")
        first = TaskEvent(
            task_id=task["id"], event_type="task.created",
            payload={"source": "desktop", "meta": {"attempt": 1}},
            event_id="event-1", occurred_at="2026-01-01T00:00:00+00:00",
        )
        second = TaskEvent(
            task_id=task["id"], event_type="task.started",
            payload={"worker": "planner"},
            event_id="event-2", occurred_at="2026-01-01T00:00:01+00:00",
        )
        self.ledger.append_event(first)
        self.ledger.append_event(second)
        reopened = TaskLedger(self.path)
        events = reopened.list_events(task["id"])
        self.assertEqual([event["event_id"] for event in events], ["event-1", "event-2"])
        self.assertEqual(events[0]["payload"], {"source": "desktop", "meta": {"attempt": 1}})
        self.assertEqual(events[1]["event_type"], "task.started")

    def test_appending_same_event_is_idempotent_but_conflicts_are_rejected(self):
        task = self.ledger.create("Idempotent events")
        event = TaskEvent(
            task_id=task["id"], event_type="tool.completed",
            payload={"call_id": "call-1", "ok": True},
            event_id="event-stable", occurred_at="2026-01-01T00:00:00+00:00",
        )
        first = self.ledger.append_event(event)
        second = self.ledger.append_event(event)
        self.assertEqual(first, second)
        self.assertEqual(len(self.ledger.list_events(task["id"])), 1)
        conflicting = TaskEvent(
            task_id=task["id"], event_type="tool.failed",
            payload={"call_id": "call-1", "ok": False},
            event_id="event-stable", occurred_at="2026-01-01T00:00:01+00:00",
        )
        with self.assertRaisesRegex(ValueError, "different content"):
            self.ledger.append_event(conflicting)

    def test_event_append_validates_task_and_list_limits(self):
        event = TaskEvent("task-missing", "task.created", {}, event_id="event-missing")
        with self.assertRaisesRegex(KeyError, "Task not found"):
            self.ledger.append_event(event)
        task = self.ledger.create("Event limit")
        with self.assertRaisesRegex(ValueError, "between 1 and 500"):
            self.ledger.list_events(task["id"], limit=0)
        with self.assertRaisesRegex(KeyError, "Task not found"):
            self.ledger.list_events("missing")

    def test_status_set_is_explicit(self):
        self.assertEqual(TASK_STATUSES, {
            "queued", "running", "awaiting_approval", "awaiting_verification",
            "completed", "failed", "cancelled"
        })


if __name__ == "__main__":
    unittest.main()
