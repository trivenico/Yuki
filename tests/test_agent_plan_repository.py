from __future__ import annotations

import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from automation.agent_plan_contract import agent_plan_request_digest
from automation.agent_plan_repository import (
    AgentPlanRecord,
    PlanSubtaskRecord,
    SQLiteAgentPlanRepository,
)


class SQLiteAgentPlanRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_path = str(Path(self.temp_dir.name) / "plans.sqlite3")
        self.repository = SQLiteAgentPlanRepository(self.database_path)

    def tearDown(self):
        self.temp_dir.cleanup()

    def _plan(self, *, tenant="tenant-a", owner="user-a", plan_id="plan-a",
              key="tenant-a:user-a:key-a", digest=None, description="Research the request"):
        canonical = [{
            "subtask_id": "research",
            "agent_id": "research-agent",
            "action": "emit",
            "description": description,
            "required_capabilities": ["research"],
            "required_permissions": ["task:enqueue"],
            "depends_on": [],
            "requires_approval": True,
        }]
        if digest is None:
            digest = agent_plan_request_digest(
                tenant_id=tenant, owner_principal_id=owner, subtasks=canonical,
            )
        return AgentPlanRecord(
            plan_id=plan_id,
            idempotency_key=key,
            request_digest=digest,
            created_at="2026-11-24T00:00:00+00:00",
            tenant_id=tenant,
            owner_principal_id=owner,
            client_idempotency_key="key-a",
            subtasks=(
                PlanSubtaskRecord(
                    subtask_id="research",
                    agent_id="research-agent",
                    action="emit",
                    description=description,
                    required_capabilities=("research",),
                    required_permissions=("task:enqueue",),
                    depends_on=(),
                    requires_approval=True,
                ),
            ),
        )

    def test_create_is_atomic_and_idempotent_across_instances(self):
        created = self.repository.create_plan(self._plan())
        second_instance = SQLiteAgentPlanRepository(self.database_path)
        replay = second_instance.create_plan(self._plan(plan_id="ignored-plan"))
        self.assertEqual(created, replay)
        self.assertEqual("plan-a", replay.plan_id)
        self.assertEqual(("research",), replay.subtasks[0].required_capabilities)

    def test_idempotency_rejects_changed_payload(self):
        self.repository.create_plan(self._plan())
        with self.assertRaisesRegex(ValueError, "payload_conflict"):
            self.repository.create_plan(self._plan(description="Research a different request", plan_id="plan-b"))

    def test_reads_hide_foreign_and_missing_plans(self):
        self.repository.create_plan(self._plan())
        self.assertIsNone(self.repository.get_plan(
            plan_id="plan-a", tenant_id="tenant-b", owner_principal_id="user-b"
        ))
        self.assertIsNone(self.repository.get_plan(
            plan_id="missing", tenant_id="tenant-a", owner_principal_id="user-a"
        ))
        self.assertIsNotNone(self.repository.get_plan(
            plan_id="plan-a", tenant_id="tenant-a", owner_principal_id="user-a"
        ))

    def test_subtask_update_is_owner_scoped_and_compare_and_set(self):
        self.repository.create_plan(self._plan())
        self.assertFalse(self.repository.update_subtask(
            plan_id="plan-a", subtask_id="research", tenant_id="tenant-b",
            owner_principal_id="user-b", status="queued", task_id="task-1",
        ))
        self.assertFalse(self.repository.update_subtask(
            plan_id="plan-a", subtask_id="research", tenant_id="tenant-a",
            owner_principal_id="user-a", status="queued", task_id="task-1",
            expected_statuses=("succeeded",),
        ))
        self.assertTrue(self.repository.update_subtask(
            plan_id="plan-a", subtask_id="research", tenant_id="tenant-a",
            owner_principal_id="user-a", status="queued", task_id="task-1",
            expected_statuses=("pending",),
        ))
        plan = self.repository.get_plan(
            plan_id="plan-a", tenant_id="tenant-a", owner_principal_id="user-a"
        )
        self.assertEqual("task-1", plan.subtasks[0].task_id)
        self.assertEqual("queued", plan.subtasks[0].status)

    def test_repository_connection_is_closed_after_context(self):
        with self.repository._connection() as connection:
            connection.execute("SELECT 1")
        with self.assertRaisesRegex(Exception, "closed"):
            connection.execute("SELECT 1")

    def test_legacy_schema_is_upgraded_without_losing_existing_plan(self):
        legacy_path = str(Path(self.temp_dir.name) / "legacy.sqlite3")
        legacy = sqlite3.connect(legacy_path)
        legacy.executescript("""
            CREATE TABLE agent_plans (
                plan_id TEXT PRIMARY KEY,
                idempotency_key TEXT NOT NULL UNIQUE,
                request_digest TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE agent_plan_subtasks (
                plan_id TEXT NOT NULL,
                subtask_id TEXT NOT NULL,
                agent_id TEXT NOT NULL,
                description TEXT NOT NULL,
                capabilities_json TEXT NOT NULL,
                dependencies_json TEXT NOT NULL,
                requires_approval INTEGER NOT NULL,
                task_id TEXT,
                status TEXT NOT NULL,
                PRIMARY KEY(plan_id, subtask_id)
            );
            INSERT INTO agent_plans VALUES (
                'legacy-plan', 'legacy-key', 'legacy-digest',
                '2025-01-01T00:00:00+00:00'
            );
            INSERT INTO agent_plan_subtasks VALUES (
                'legacy-plan', 'legacy-task', 'legacy-agent', 'Old task',
                '["read"]', '[]', 0, NULL, 'pending'
            );
        """)
        legacy.close()

        upgraded = SQLiteAgentPlanRepository(legacy_path)
        record = upgraded.get_plan(
            plan_id="legacy-plan", tenant_id=None, owner_principal_id=None
        )
        self.assertIsNotNone(record)
        self.assertEqual("legacy-key", record.client_idempotency_key)
        self.assertEqual("emit", record.subtasks[0].action)
        self.assertEqual((), record.subtasks[0].required_permissions)

    def test_concurrent_same_key_submission_returns_one_plan(self):
        second = SQLiteAgentPlanRepository(self.database_path)
        plan = self._plan()
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(repository.create_plan, plan)
                for repository in (self.repository, second)
            ]
            results = [future.result(timeout=15) for future in futures]
        self.assertEqual(results[0], results[1])
        with self.repository._connection() as connection:
            count = connection.execute("SELECT COUNT(*) FROM agent_plans").fetchone()[0]
        self.assertEqual(1, count)

    def test_owner_scope_must_be_complete(self):
        with self.assertRaisesRegex(ValueError, "owner_scope_invalid"):
            self.repository.get_plan(
                plan_id="plan-a", tenant_id="tenant-a", owner_principal_id=None
            )


    def test_dispatch_reservation_takeover_fences_stale_completion(self):
        self.repository.create_plan(self._plan())
        first = self.repository.reserve_dispatch(
            plan_id="plan-a", subtask_id="research",
            tenant_id="tenant-a", owner_principal_id="user-a",
        )
        second_repository = SQLiteAgentPlanRepository(self.database_path)
        second = second_repository.reserve_dispatch(
            plan_id="plan-a", subtask_id="research",
            tenant_id="tenant-a", owner_principal_id="user-a",
        )
        self.assertEqual("reserved", first.state)
        self.assertEqual(first.generation + 1, second.generation)
        self.assertNotEqual(first.claim_token, second.claim_token)
        self.assertFalse(self.repository.complete_dispatch(
            plan_id="plan-a", subtask_id="research",
            tenant_id="tenant-a", owner_principal_id="user-a",
            claim_token=first.claim_token, generation=first.generation,
            task_id="stale-task", status="queued",
        ))
        self.assertTrue(second_repository.complete_dispatch(
            plan_id="plan-a", subtask_id="research",
            tenant_id="tenant-a", owner_principal_id="user-a",
            claim_token=second.claim_token, generation=second.generation,
            task_id="task-current", status="queued",
        ))
        persisted = self.repository.get_plan(
            plan_id="plan-a", tenant_id="tenant-a", owner_principal_id="user-a"
        )
        self.assertEqual("task-current", persisted.subtasks[0].task_id)
        self.assertEqual("queued", persisted.subtasks[0].status)
        replay = self.repository.reserve_dispatch(
            plan_id="plan-a", subtask_id="research",
            tenant_id="tenant-a", owner_principal_id="user-a",
        )
        self.assertEqual("enqueued", replay.state)
        self.assertEqual("task-current", replay.task_id)

    def test_cancelled_dispatch_reservation_cannot_be_reclaimed(self):
        self.repository.create_plan(self._plan())
        reservation = self.repository.reserve_dispatch(
            plan_id="plan-a", subtask_id="research",
            tenant_id="tenant-a", owner_principal_id="user-a",
        )
        self.assertTrue(self.repository.cancel_dispatch(
            plan_id="plan-a", subtask_id="research",
            tenant_id="tenant-a", owner_principal_id="user-a",
        ))
        self.assertFalse(self.repository.complete_dispatch(
            plan_id="plan-a", subtask_id="research",
            tenant_id="tenant-a", owner_principal_id="user-a",
            claim_token=reservation.claim_token, generation=reservation.generation,
            task_id="late-task", status="queued",
        ))
        replay = self.repository.reserve_dispatch(
            plan_id="plan-a", subtask_id="research",
            tenant_id="tenant-a", owner_principal_id="user-a",
        )
        self.assertEqual("cancelled", replay.state)
        self.assertIsNone(replay.task_id)

    def test_cancellation_before_first_reservation_persists_tombstone(self):
        self.repository.create_plan(self._plan())
        self.assertTrue(self.repository.cancel_dispatch(
            plan_id="plan-a", subtask_id="research",
            tenant_id="tenant-a", owner_principal_id="user-a",
        ))
        tombstone = self.repository.get_dispatch(
            plan_id="plan-a", subtask_id="research",
            tenant_id="tenant-a", owner_principal_id="user-a",
        )
        self.assertEqual("cancelled", tombstone.state)
        self.assertEqual(1, tombstone.generation)
        self.assertIsNone(tombstone.claim_token)

        late_claim = self.repository.reserve_dispatch(
            plan_id="plan-a", subtask_id="research",
            tenant_id="tenant-a", owner_principal_id="user-a",
        )
        self.assertEqual("cancelled", late_claim.state)
        self.assertIsNone(late_claim.claim_token)
        self.assertIsNone(late_claim.task_id)
        self.assertTrue(self.repository.cancel_dispatch(
            plan_id="plan-a", subtask_id="research",
            tenant_id="tenant-a", owner_principal_id="user-a",
        ))

    def test_dispatch_reservation_is_owner_scoped(self):
        self.repository.create_plan(self._plan())
        self.assertIsNone(self.repository.reserve_dispatch(
            plan_id="plan-a", subtask_id="research",
            tenant_id="tenant-b", owner_principal_id="user-b",
        ))
        self.assertIsNone(self.repository.get_dispatch(
            plan_id="plan-a", subtask_id="research",
            tenant_id="tenant-b", owner_principal_id="user-b",
        ))

if __name__ == "__main__":
    unittest.main()
