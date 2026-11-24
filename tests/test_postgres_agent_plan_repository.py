"""PostgreSQL integration tests for durable agent plans and dispatch fencing.

Set YUKI_TEST_POSTGRES_URL to a disposable PostgreSQL database. These tests
skip when PostgreSQL is unavailable.
"""
import hashlib
import json
import os
from pathlib import Path
import threading
import unittest
import uuid

POSTGRES_URL = os.environ.get("YUKI_TEST_POSTGRES_URL")
try:
    import psycopg
except ImportError:  # pragma: no cover
    psycopg = None

_ENABLED = bool(POSTGRES_URL and psycopg)


@unittest.skipUnless(_ENABLED, "YUKI_TEST_POSTGRES_URL and psycopg are required")
class PostgresAgentPlanRepositoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from automation.postgres_agent_plan_repository import PostgresAgentPlanRepository
        cls.repository_type = PostgresAgentPlanRepository
        root = Path(__file__).resolve().parents[1]
        with psycopg.connect(POSTGRES_URL) as conn:
            for name in ("0007_workflow_runs.sql", "0009_external_operations.sql"):
                conn.execute((root / "migrations/security" / name).read_text(encoding="utf-8"), prepare=False)
            owned = conn.execute(
                "SELECT 1 FROM pg_constraint WHERE conname='workflow_runs_ownership_status_check'"
            ).fetchone()
            if not owned:
                conn.execute(
                    (root / "migrations/security/0010_workflow_task_ownership.sql").read_text(encoding="utf-8"),
                    prepare=False,
                )
            conn.execute(
                (root / "migrations/security/0012_agent_plan_repository.sql").read_text(encoding="utf-8"),
                prepare=False,
            )

    def setUp(self):
        self.repo_a = self.repository_type(POSTGRES_URL, max_pool_size=4)
        self.repo_b = self.repository_type(POSTGRES_URL, max_pool_size=4)
        self.tenant = "tenant-" + uuid.uuid4().hex
        self.owner = "human-" + uuid.uuid4().hex
        self.key = "plan-" + uuid.uuid4().hex
        subtasks = [{
            "subtask_id": "research", "agent_id": "researcher",
            "description": "Gather bounded evidence", "capabilities": ["research"],
            "permissions": ["web:read"], "dependencies": [],
            "requires_approval": True,
        }]
        canonical = json.dumps(subtasks, sort_keys=True, separators=(",", ":"))
        self.digest = hashlib.sha256(canonical.encode()).hexdigest()
        self.plan = self.repo_a.create_plan(
            tenant_id=self.tenant, owner_principal_id=self.owner,
            client_idempotency_key=self.key, request_digest=self.digest,
            subtasks=subtasks,
        )

    def tearDown(self):
        with psycopg.connect(POSTGRES_URL) as conn:
            conn.execute("DELETE FROM agent_plans WHERE tenant_id=%s AND owner_principal_id=%s",
                         (self.tenant, self.owner))
        self.repo_a.close()
        self.repo_b.close()

    def test_plan_idempotency_is_owner_scoped_and_payload_bound(self):
        same = self.repo_b.create_plan(
            tenant_id=self.tenant, owner_principal_id=self.owner,
            client_idempotency_key=self.key, request_digest=self.digest,
            subtasks=[{"subtask_id": "research", "agent_id": "researcher",
                       "description": "Gather bounded evidence"}],
        )
        self.assertEqual(same["plan"]["plan_id"], self.plan["plan"]["plan_id"])
        with self.assertRaises(KeyError):
            self.repo_b.get_plan(
                tenant_id=self.tenant, owner_principal_id="human-other",
                plan_id=self.plan["plan"]["plan_id"],
            )
        with self.assertRaises(Exception):
            self.repo_b.create_plan(
                tenant_id=self.tenant, owner_principal_id=self.owner,
                client_idempotency_key=self.key,
                request_digest="0" * 64,
                subtasks=[{"subtask_id": "research", "agent_id": "researcher",
                           "description": "Different payload"}],
            )

    def test_only_one_instance_can_reserve_active_subtask(self):
        barrier = threading.Barrier(2)
        results = []
        errors = []
        def reserve(repo):
            try:
                barrier.wait(timeout=10)
                results.append(repo.reserve_dispatch(
                    tenant_id=self.tenant, owner_principal_id=self.owner,
                    plan_id=self.plan["plan"]["plan_id"], subtask_id="research",
                ))
            except Exception as exc:  # surfaced in assertion below
                errors.append(exc)
        threads = [
            threading.Thread(target=reserve, args=(self.repo_a,)),
            threading.Thread(target=reserve, args=(self.repo_b,)),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=15)
        self.assertFalse(errors, errors)
        self.assertEqual(sum(item is not None for item in results), 1)

    def test_reservation_generation_fences_expired_coordinator(self):
        first = self.repo_a.reserve_dispatch(
            tenant_id=self.tenant, owner_principal_id=self.owner,
            plan_id=self.plan["plan"]["plan_id"], subtask_id="research", ttl_seconds=5,
        )
        self.assertIsNotNone(first)
        with psycopg.connect(POSTGRES_URL) as conn:
            conn.execute(
                "UPDATE agent_plan_dispatch_reservations SET expires_at=clock_timestamp()-interval '1 second' "
                "WHERE tenant_id=%s AND owner_principal_id=%s AND plan_id=%s AND subtask_id='research'",
                (self.tenant, self.owner, self.plan["plan"]["plan_id"]),
            )
        second = self.repo_b.reserve_dispatch(
            tenant_id=self.tenant, owner_principal_id=self.owner,
            plan_id=self.plan["plan"]["plan_id"], subtask_id="research",
        )
        self.assertGreater(second.generation, first.generation)
        active = self.repo_b.claim_outbox(limit=20)
        active_for_plan = [row for row in active if row["plan_id"] == self.plan["plan"]["plan_id"]]
        self.assertEqual(len(active_for_plan), 1)
        self.assertEqual(active_for_plan[0]["dispatch_generation"], second.generation)
        self.assertEqual(active_for_plan[0]["reservation_token"], second.reservation_token)
        self.assertFalse(self.repo_a.mark_enqueued(
            tenant_id=self.tenant, owner_principal_id=self.owner,
            plan_id=self.plan["plan"]["plan_id"], subtask_id="research",
            reservation_token=first.reservation_token, generation=first.generation,
            task_id="task-old",
        ))

    def test_outbox_claim_is_exclusive_and_stale_processing_is_recoverable(self):
        reservation = self.repo_a.reserve_dispatch(
            tenant_id=self.tenant, owner_principal_id=self.owner,
            plan_id=self.plan["plan"]["plan_id"], subtask_id="research",
        )
        claimed_a = self.repo_a.claim_outbox(limit=10)
        claimed_b = self.repo_b.claim_outbox(limit=10)
        target_a = [row for row in claimed_a if row["plan_id"] == self.plan["plan"]["plan_id"]]
        target_b = [row for row in claimed_b if row["plan_id"] == self.plan["plan"]["plan_id"]]
        self.assertEqual(len(target_a), 1)
        outbox_id = target_a[0]["outbox_id"]
        self.assertNotIn(outbox_id, [row["outbox_id"] for row in target_b])
        with psycopg.connect(POSTGRES_URL) as conn:
            conn.execute(
                "UPDATE agent_plan_dispatch_outbox SET updated_at=clock_timestamp()-interval '120 seconds' "
                "WHERE outbox_id=%s", (outbox_id,),
            )
        recovered = self.repo_b.recover_stale_outbox(stale_after_seconds=60)
        self.assertEqual(recovered["requeued"], 1)
        reclaimed = self.repo_b.claim_outbox(limit=10)
        self.assertTrue(any(row["outbox_id"] == outbox_id for row in reclaimed))


    def test_expired_reservation_retires_its_unclaimable_outbox_generation(self):
        first = self.repo_a.reserve_dispatch(
            tenant_id=self.tenant, owner_principal_id=self.owner,
            plan_id=self.plan["plan"]["plan_id"], subtask_id="research",
        )
        with psycopg.connect(POSTGRES_URL) as conn:
            conn.execute(
                "UPDATE agent_plan_dispatch_reservations SET expires_at=clock_timestamp()-interval '1 second' "
                "WHERE tenant_id=%s AND owner_principal_id=%s AND plan_id=%s AND subtask_id='research'",
                (self.tenant, self.owner, self.plan["plan"]["plan_id"]),
            )
        self.assertEqual(self.repo_b.claim_outbox(limit=20), [])
        with psycopg.connect(POSTGRES_URL) as conn:
            state = conn.execute(
                "SELECT state FROM agent_plan_dispatch_outbox WHERE tenant_id=%s AND owner_principal_id=%s "
                "AND plan_id=%s AND dispatch_generation=%s",
                (self.tenant, self.owner, self.plan["plan"]["plan_id"], first.generation),
            ).fetchone()[0]
        self.assertEqual(state, "dead_letter")

    def test_mark_enqueued_rolls_back_if_subtask_generation_changed(self):
        reservation = self.repo_a.reserve_dispatch(
            tenant_id=self.tenant, owner_principal_id=self.owner,
            plan_id=self.plan["plan"]["plan_id"], subtask_id="research",
        )
        self.repo_a.claim_outbox(limit=20)
        with psycopg.connect(POSTGRES_URL) as conn:
            conn.execute(
                "UPDATE agent_plan_subtasks SET dispatch_generation=dispatch_generation+1 "
                "WHERE tenant_id=%s AND owner_principal_id=%s AND plan_id=%s AND subtask_id='research'",
                (self.tenant, self.owner, self.plan["plan"]["plan_id"]),
            )
        from automation.postgres_agent_plan_repository import AgentPlanRepositoryConflict
        with self.assertRaises(AgentPlanRepositoryConflict):
            self.repo_a.mark_enqueued(
                tenant_id=self.tenant, owner_principal_id=self.owner,
                plan_id=self.plan["plan"]["plan_id"], subtask_id="research",
                reservation_token=reservation.reservation_token,
                generation=reservation.generation, task_id="task-test",
            )
        with psycopg.connect(POSTGRES_URL) as conn:
            state = conn.execute(
                "SELECT state FROM agent_plan_dispatch_reservations WHERE tenant_id=%s AND owner_principal_id=%s "
                "AND plan_id=%s AND subtask_id='research'",
                (self.tenant, self.owner, self.plan["plan"]["plan_id"]),
            ).fetchone()[0]
        self.assertEqual(state, "reserved")
