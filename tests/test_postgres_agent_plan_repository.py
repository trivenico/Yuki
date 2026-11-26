"""PostgreSQL integration tests for durable agent plans and dispatch fencing.

Set YUKI_TEST_POSTGRES_URL to a disposable PostgreSQL database. These tests
skip when PostgreSQL is unavailable.
"""
import os
from pathlib import Path
import threading
import unittest
import uuid

from automation.agent_plan_contract import agent_plan_request_digest

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
            conn.execute(
                (root / "migrations/security/0013_agent_plan_outbox_claim_fencing.sql").read_text(encoding="utf-8"),
                prepare=False,
            )
            conn.execute(
                (root / "migrations/security/0014_agent_plan_subtask_action.sql").read_text(encoding="utf-8"),
                prepare=False,
            )
            conn.execute(
                (root / "migrations/security/0015_agent_plan_dispatch_cancellation.sql").read_text(encoding="utf-8"),
                prepare=False,
            )
            conn.execute(
                (root / "migrations/security/0018_agent_plan_dispatch_envelope.sql").read_text(encoding="utf-8"),
                prepare=False,
            )
            conn.execute(
                (root / "migrations/security/0019_agent_plan_dispatch_failure_code.sql").read_text(encoding="utf-8"),
                prepare=False,
            )
            conn.execute(
                (root / "migrations/security/0020_workflow_dispatch_visibility_gate.sql").read_text(encoding="utf-8"),
                prepare=False,
            )
            goals = conn.execute("SELECT to_regclass('public.workflow_goals') AS table_name").fetchone()
            if goals["table_name"] is None:
                conn.execute(
                    (root / "migrations/security/0021_distributed_workflow_goals.sql").read_text(encoding="utf-8"),
                    prepare=False,
                )
            dependency_ready = conn.execute(
                "SELECT 1 FROM information_schema.columns WHERE table_name='workflow_runs' AND column_name='dependency_ready'"
            ).fetchone()
            if dependency_ready is None:
                conn.execute(
                    (root / "migrations/security/0022_workflow_dependency_readiness.sql").read_text(encoding="utf-8"),
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
            "action": "summarize", "description": "Gather bounded evidence", "capabilities": ["research"],
            "permissions": ["web:read"], "dependencies": [],
            "requires_approval": True,
        }]
        self.digest = agent_plan_request_digest(
            tenant_id=self.tenant, owner_principal_id=self.owner, subtasks=subtasks,
        )
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


    def _create_held_task(self, suffix):
        from automation.postgres_workflow_store import PostgresWorkflowRunStore
        store = PostgresWorkflowRunStore(POSTGRES_URL, min_pool_size=1, max_pool_size=2)
        try:
            task = store.create(
                idempotency_key="dispatch-gate:" + self.tenant + ":" + suffix,
                workflow_name="specialist-dispatch-test",
                definition=[{
                    "name": "research", "action": "summarize",
                    "argument": "bounded test argument", "requires_approval": False,
                }],
                tenant_id=self.tenant, owner_principal_id=self.owner,
                dispatch_ready=False,
            )
            return task.run_id
        finally:
            store.close()

    def _claim_outbox(self, repository, *, limit=20):
        """Test helper: materialize a bounded version-1 envelope for this fixture's pending intents."""
        with psycopg.connect(POSTGRES_URL) as conn:
            rows = conn.execute(
                """SELECT plan_id,subtask_id,dispatch_generation
                   FROM agent_plan_dispatch_outbox
                   WHERE tenant_id=%s AND owner_principal_id=%s
                     AND state='pending' AND envelope_json IS NULL""",
                (self.tenant, self.owner),
            ).fetchall()
        for row in rows:
            reservation = repository.get_dispatch(
                tenant_id=self.tenant, owner_principal_id=self.owner,
                plan_id=row[0], subtask_id=row[1],
            )
            if reservation is None or reservation.state != "reserved":
                continue
            subtasks = repository.list_subtasks(
                tenant_id=self.tenant, owner_principal_id=self.owner,
                plan_id=row[0],
            )
            subtask = next(item for item in subtasks if item.subtask_id == row[1])
            repository.attach_dispatch_envelope(
                tenant_id=self.tenant, owner_principal_id=self.owner,
                plan_id=row[0], subtask_id=row[1],
                reservation_token=reservation.reservation_token,
                generation=reservation.generation,
                envelope={
                    "version": 1, "tenant_id": self.tenant,
                    "owner_principal_id": self.owner, "plan_id": row[0],
                    "subtask_id": row[1],
                    "reservation_generation": reservation.generation,
                    "idempotency_key": f"agent-plan:{row[0]}:{row[1]}",
                    "agent_id": subtask.agent_id, "action": subtask.action,
                    "manifest_revision": 1,
                    "required_capabilities": list(subtask.required_capabilities),
                    "required_permissions": list(subtask.required_permissions),
                    "requires_approval": subtask.requires_approval,
                    "dependency_snapshot": [],
                    "dependency_output_digests": {},
                    "argument": subtask.description,
                },
            )
        return repository.claim_outbox(limit=limit)



    def test_outbox_digest_tampering_is_dead_lettered_before_handoff(self):
        plan_id = self.plan["plan"]["plan_id"]
        reservation = self.repo_a.reserve_dispatch(
            tenant_id=self.tenant, owner_principal_id=self.owner,
            plan_id=plan_id, subtask_id="research",
        )
        self.assertIsNotNone(reservation)
        subtasks = self.repo_a.list_subtasks(
            tenant_id=self.tenant, owner_principal_id=self.owner, plan_id=plan_id,
        )
        subtask = next(item for item in subtasks if item.subtask_id == "research")
        self.repo_a.attach_dispatch_envelope(
            tenant_id=self.tenant, owner_principal_id=self.owner,
            plan_id=plan_id, subtask_id="research",
            reservation_token=reservation.reservation_token,
            generation=reservation.generation,
            envelope={
                "version": 1, "tenant_id": self.tenant,
                "owner_principal_id": self.owner, "plan_id": plan_id,
                "subtask_id": "research",
                "reservation_generation": reservation.generation,
                "idempotency_key": f"agent-plan:{plan_id}:research",
                "agent_id": subtask.agent_id, "action": subtask.action,
                "manifest_revision": 1,
                "required_capabilities": list(subtask.required_capabilities),
                "required_permissions": list(subtask.required_permissions),
                "requires_approval": subtask.requires_approval,
                "dependency_snapshot": [], "dependency_output_digests": {},
                "argument": subtask.description,
            },
        )
        with psycopg.connect(POSTGRES_URL) as conn:
            conn.execute(
                """UPDATE agent_plan_dispatch_outbox
                   SET envelope_digest=%s
                   WHERE tenant_id=%s AND owner_principal_id=%s
                     AND plan_id=%s AND subtask_id=%s""",
                ("0" * 64, self.tenant, self.owner, plan_id, "research"),
            )
        self.assertEqual([], self.repo_a.claim_outbox(limit=20))
        with psycopg.connect(POSTGRES_URL) as conn:
            state = conn.execute(
                """SELECT state FROM agent_plan_dispatch_outbox
                   WHERE tenant_id=%s AND owner_principal_id=%s
                     AND plan_id=%s AND subtask_id=%s""",
                (self.tenant, self.owner, plan_id, "research"),
            ).fetchone()[0]
        self.assertEqual("dead_letter", state)

    def test_dispatch_envelope_rejects_malformed_nested_dependency_data(self):
        plan_id = self.plan["plan"]["plan_id"]
        reservation = self.repo_a.reserve_dispatch(
            tenant_id=self.tenant, owner_principal_id=self.owner,
            plan_id=plan_id, subtask_id="research",
        )
        base = {
            "version": 1, "tenant_id": self.tenant,
            "owner_principal_id": self.owner, "plan_id": plan_id,
            "subtask_id": "research",
            "reservation_generation": reservation.generation,
            "idempotency_key": f"agent-plan:{plan_id}:research",
            "agent_id": "researcher", "action": "summarize",
            "manifest_revision": 1, "required_capabilities": ["research"],
            "required_permissions": ["web:read"], "requires_approval": True,
            "dependency_snapshot": [], "dependency_output_digests": {},
            "argument": "Gather bounded evidence",
        }
        malformed = [
            ({**base, "dependency_snapshot": [{"subtask_id": "dep", "task_id": "task-1", "state": "running"}]}, "state"),
            ({**base, "dependency_snapshot": [{"subtask_id": "dep", "task_id": "task-1", "state": []}]}, "unhashable_state_list"),
            ({**base, "dependency_snapshot": [{"subtask_id": "dep", "task_id": "task-1", "state": {}}]}, "unhashable_state_object"),
            ({**base, "dependency_snapshot": [{"subtask_id": "dep", "task_id": "task-1", "state": "succeeded", "extra": True}]}, "shape"),
            ({**base, "dependency_output_digests": {"unknown": "a" * 64}}, "reference"),
            ({**base, "dependency_snapshot": [{"subtask_id": "dep", "task_id": "task-1", "state": "succeeded"}],
              "dependency_output_digests": {"dep": "not-a-digest"}}, "digest"),
        ]
        for envelope, label in malformed:
            with self.subTest(label=label):
                with self.assertRaises(ValueError):
                    self.repo_a.attach_dispatch_envelope(
                        tenant_id=self.tenant, owner_principal_id=self.owner,
                        plan_id=plan_id, subtask_id="research",
                        reservation_token=reservation.reservation_token,
                        generation=reservation.generation, envelope=envelope,
                    )

    def test_typed_adapter_claim_returns_scoped_immutable_dispatch_record(self):
        from automation.postgres_agent_plan_adapter import (
            ClaimedAgentPlanDispatch, PostgresAgentPlanRepositoryAdapter,
        )

        adapter = PostgresAgentPlanRepositoryAdapter(self.repo_a)
        plan_id = self.plan["plan"]["plan_id"]
        self.repo_a.reserve_dispatch(
            tenant_id=self.tenant, owner_principal_id=self.owner,
            plan_id=plan_id, subtask_id="research",
        )
        claims = adapter.claim_outbox(limit=20)  # no envelope: fail closed
        self.assertEqual([], claims)
        self._claim_outbox(self.repo_a, limit=20)  # helper claims and returns raw repository rows
        # A fresh reservation generation is intentionally not created; use the
        # adapter on a separate fixture row in a dedicated plan.
        plan = self.repo_a.create_plan(
            tenant_id=self.tenant, owner_principal_id=self.owner,
            client_idempotency_key=self.key + "-typed-claim",
            request_digest=agent_plan_request_digest(
                tenant_id=self.tenant, owner_principal_id=self.owner,
                subtasks=[{
                    "subtask_id": "typed", "agent_id": "researcher",
                    "action": "summarize", "description": "Typed claim test",
                    "required_capabilities": ["research"],
                    "required_permissions": ["web:read"],
                    "depends_on": [], "requires_approval": True,
                }],
            ),
            subtasks=[{
                "subtask_id": "typed", "agent_id": "researcher",
                "action": "summarize", "description": "Typed claim test",
                "capabilities": ["research"], "permissions": ["web:read"],
                "dependencies": [], "requires_approval": True,
            }],
        )
        typed_plan_id = plan["plan"]["plan_id"]
        reservation = self.repo_a.reserve_dispatch(
            tenant_id=self.tenant, owner_principal_id=self.owner,
            plan_id=typed_plan_id, subtask_id="typed",
        )
        subtask = self.repo_a.list_subtasks(
            tenant_id=self.tenant, owner_principal_id=self.owner, plan_id=typed_plan_id,
        )[0]
        self.repo_a.attach_dispatch_envelope(
            tenant_id=self.tenant, owner_principal_id=self.owner,
            plan_id=typed_plan_id, subtask_id="typed",
            reservation_token=reservation.reservation_token,
            generation=reservation.generation,
            envelope={
                "version": 1, "tenant_id": self.tenant,
                "owner_principal_id": self.owner, "plan_id": typed_plan_id,
                "subtask_id": "typed", "reservation_generation": reservation.generation,
                "idempotency_key": f"agent-plan:{typed_plan_id}:typed",
                "agent_id": subtask.agent_id, "action": subtask.action,
                "manifest_revision": 1, "required_capabilities": list(subtask.required_capabilities),
                "required_permissions": list(subtask.required_permissions),
                "requires_approval": subtask.requires_approval,
                "dependency_snapshot": [], "dependency_output_digests": {},
                "argument": subtask.description,
            },
        )
        typed = adapter.claim_outbox(limit=20)
        claim = next(item for item in typed if item.plan_id == typed_plan_id)
        self.assertIsInstance(claim, ClaimedAgentPlanDispatch)
        self.assertEqual(self.tenant, claim.tenant_id)
        self.assertEqual(self.owner, claim.owner_principal_id)
        self.assertEqual(reservation.generation, claim.dispatch_generation)
        self.assertEqual(claim.envelope["plan_id"], claim.plan_id)
        self.assertTrue(claim.claim_token)
        self.assertEqual(64, len(claim.envelope_digest))
        with self.assertRaises(TypeError):
            claim.envelope["argument"] = "tampered"
        with self.assertRaises(TypeError):
            claim.envelope["required_capabilities"][0] = "tampered"


    def test_typed_adapter_forwards_worker_heartbeat_and_completion_fences(self):
        from automation.postgres_agent_plan_adapter import PostgresAgentPlanRepositoryAdapter

        adapter = PostgresAgentPlanRepositoryAdapter(self.repo_a)
        plan_id = self.plan["plan"]["plan_id"]
        reservation = self.repo_a.reserve_dispatch(
            tenant_id=self.tenant, owner_principal_id=self.owner,
            plan_id=plan_id, subtask_id="research",
        )
        claimed = self._claim_outbox(self.repo_a, limit=20)
        row = next(item for item in claimed if item["plan_id"] == plan_id)
        self.assertTrue(adapter.heartbeat_outbox_claim(
            tenant_id=self.tenant, owner_principal_id=self.owner,
            plan_id=plan_id, subtask_id="research",
            reservation_token=reservation.reservation_token,
            generation=reservation.generation,
            claim_token=row["claim_token"],
            claim_generation=row["claim_generation"],
        ))
        held_task_id = self._create_held_task("adapter")
        self.assertTrue(adapter.complete_claimed_dispatch(
            tenant_id=self.tenant, owner_principal_id=self.owner,
            plan_id=plan_id, subtask_id="research",
            reservation_token=reservation.reservation_token,
            dispatch_generation=reservation.generation,
            outbox_claim_token=row["claim_token"],
            outbox_claim_generation=row["claim_generation"],
            task_id=held_task_id, status="queued",
        ))

    def test_permanent_dispatch_failure_requires_both_fences_and_is_atomic(self):
        plan_id = self.plan["plan"]["plan_id"]
        reservation = self.repo_a.reserve_dispatch(
            tenant_id=self.tenant, owner_principal_id=self.owner,
            plan_id=plan_id, subtask_id="research",
        )
        claimed = self._claim_outbox(self.repo_a, limit=20)
        row = next(item for item in claimed if item["plan_id"] == plan_id)
        args = dict(
            tenant_id=self.tenant, owner_principal_id=self.owner,
            plan_id=plan_id, subtask_id="research",
            reservation_token=reservation.reservation_token,
            dispatch_generation=reservation.generation,
            outbox_claim_token=row["claim_token"],
            outbox_claim_generation=row["claim_generation"],
            failure_code="manifest_revision_changed",
        )
        self.assertTrue(self.repo_b.dead_letter_claimed_dispatch(**args))
        self.assertFalse(self.repo_a.dead_letter_claimed_dispatch(**args))
        with psycopg.connect(POSTGRES_URL) as conn:
            outbox = conn.execute(
                """SELECT state,failure_code,claim_token FROM agent_plan_dispatch_outbox
                   WHERE tenant_id=%s AND owner_principal_id=%s AND plan_id=%s AND subtask_id=%s""",
                (self.tenant, self.owner, plan_id, "research"),
            ).fetchone()
            reservation_state = conn.execute(
                """SELECT state FROM agent_plan_dispatch_reservations
                   WHERE tenant_id=%s AND owner_principal_id=%s AND plan_id=%s AND subtask_id=%s""",
                (self.tenant, self.owner, plan_id, "research"),
            ).fetchone()[0]
            subtask_state = conn.execute(
                """SELECT status FROM agent_plan_subtasks
                   WHERE tenant_id=%s AND owner_principal_id=%s AND plan_id=%s AND subtask_id=%s""",
                (self.tenant, self.owner, plan_id, "research"),
            ).fetchone()[0]
        self.assertEqual(("dead_letter", "manifest_revision_changed", None), tuple(outbox))
        self.assertEqual("abandoned", reservation_state)
        self.assertEqual("failed", subtask_state)

    def test_permanent_dispatch_failure_rejects_invalid_classification(self):
        plan_id = self.plan["plan"]["plan_id"]
        reservation = self.repo_a.reserve_dispatch(
            tenant_id=self.tenant, owner_principal_id=self.owner,
            plan_id=plan_id, subtask_id="research",
        )
        row = next(item for item in self._claim_outbox(self.repo_a, limit=20) if item["plan_id"] == plan_id)
        with self.assertRaisesRegex(ValueError, "failure_code_invalid"):
            self.repo_a.dead_letter_claimed_dispatch(
                tenant_id=self.tenant, owner_principal_id=self.owner,
                plan_id=plan_id, subtask_id="research",
                reservation_token=reservation.reservation_token,
                dispatch_generation=reservation.generation,
                outbox_claim_token=row["claim_token"],
                outbox_claim_generation=row["claim_generation"],
                failure_code="AuthorizationError: secret",
            )
        dispatch = self.repo_a.get_dispatch(
            tenant_id=self.tenant, owner_principal_id=self.owner,
            plan_id=plan_id, subtask_id="research",
        )
        self.assertEqual("reserved", dispatch.state)

    def test_claimed_dispatch_completion_requires_both_fences(self):
        plan_id = self.plan["plan"]["plan_id"]
        self.repo_a.reserve_dispatch(
            tenant_id=self.tenant, owner_principal_id=self.owner,
            plan_id=plan_id, subtask_id="research",
        )
        claimed = self._claim_outbox(self.repo_a, limit=20)
        row = next(item for item in claimed if item["plan_id"] == plan_id)
        reservation = self.repo_b.get_dispatch(
            tenant_id=self.tenant, owner_principal_id=self.owner,
            plan_id=plan_id, subtask_id="research",
        )
        held_task_id = self._create_held_task("completion")
        with psycopg.connect(POSTGRES_URL) as conn:
            held = conn.execute(
                "SELECT dispatch_ready FROM workflow_runs WHERE run_id=%s AND tenant_id=%s AND owner_principal_id=%s",
                (held_task_id, self.tenant, self.owner),
            ).fetchone()
        self.assertEqual(False, held[0])
        self.assertTrue(self.repo_b.complete_claimed_dispatch(
            tenant_id=self.tenant, owner_principal_id=self.owner,
            plan_id=plan_id, subtask_id="research",
            reservation_token=reservation.reservation_token,
            dispatch_generation=reservation.generation,
            outbox_claim_token=row["claim_token"],
            outbox_claim_generation=row["claim_generation"],
            task_id=held_task_id, status="queued",
        ))
        with psycopg.connect(POSTGRES_URL) as conn:
            released = conn.execute(
                "SELECT dispatch_ready FROM workflow_runs WHERE run_id=%s AND tenant_id=%s AND owner_principal_id=%s",
                (held_task_id, self.tenant, self.owner),
            ).fetchone()
        self.assertEqual(True, released[0])
        replay = self.repo_a.complete_claimed_dispatch(
            tenant_id=self.tenant, owner_principal_id=self.owner,
            plan_id=plan_id, subtask_id="research",
            reservation_token=reservation.reservation_token,
            dispatch_generation=reservation.generation,
            outbox_claim_token=row["claim_token"],
            outbox_claim_generation=row["claim_generation"],
            task_id="task-dispatched-1", status="queued",
        )
        self.assertFalse(replay)
        dispatch = self.repo_a.get_dispatch(
            tenant_id=self.tenant, owner_principal_id=self.owner,
            plan_id=plan_id, subtask_id="research",
        )
        self.assertEqual("enqueued", dispatch.state)
        self.assertEqual(held_task_id, dispatch.task_id)

    def test_claimed_dispatch_completion_rejects_non_queued_status(self):
        plan_id = self.plan["plan"]["plan_id"]
        reservation = self.repo_a.reserve_dispatch(
            tenant_id=self.tenant, owner_principal_id=self.owner,
            plan_id=plan_id, subtask_id="research",
        )
        claimed = self._claim_outbox(self.repo_a, limit=20)
        row = next(item for item in claimed if item["plan_id"] == plan_id)
        with self.assertRaisesRegex(ValueError, "completion_invalid"):
            self.repo_a.complete_claimed_dispatch(
                tenant_id=self.tenant, owner_principal_id=self.owner,
                plan_id=plan_id, subtask_id="research",
                reservation_token=reservation.reservation_token,
                dispatch_generation=reservation.generation,
                outbox_claim_token=row["claim_token"],
                outbox_claim_generation=row["claim_generation"],
                task_id="task-invalid-state", status="succeeded",
            )
        # Rejected input must not consume either fence or mutate the reservation.
        dispatch = self.repo_a.get_dispatch(
            tenant_id=self.tenant, owner_principal_id=self.owner,
            plan_id=plan_id, subtask_id="research",
        )
        self.assertEqual("reserved", dispatch.state)
        self.assertIsNone(dispatch.task_id)

    def test_outbox_refuses_claim_until_immutable_envelope_is_attached(self):
        reservation = self.repo_a.reserve_dispatch(
            tenant_id=self.tenant, owner_principal_id=self.owner,
            plan_id=self.plan["plan"]["plan_id"], subtask_id="research",
        )
        self.assertIsNotNone(reservation)
        self.assertFalse(any(
            row["plan_id"] == self.plan["plan"]["plan_id"]
            for row in self.repo_a.claim_outbox(limit=20)
        ))
        claimed = self._claim_outbox(self.repo_b, limit=20)
        target = next(
            row for row in claimed
            if row["plan_id"] == self.plan["plan"]["plan_id"]
        )
        self.assertEqual(1, target["envelope_version"])
        self.assertEqual(self.tenant, target["envelope_json"]["tenant_id"])
        self.assertEqual(reservation.generation, target["envelope_json"]["reservation_generation"])
        self.assertEqual(64, len(target["envelope_digest"]))
        from automation.postgres_agent_plan_repository import AgentPlanRepositoryConflict
        with self.assertRaisesRegex(AgentPlanRepositoryConflict, "envelope_fenced"):
            self.repo_a.attach_dispatch_envelope(
                tenant_id=self.tenant, owner_principal_id=self.owner,
                plan_id=self.plan["plan"]["plan_id"], subtask_id="research",
                reservation_token=reservation.reservation_token,
                generation=reservation.generation,
                envelope=target["envelope_json"],
            )

    def test_repository_exposes_readiness_and_non_secret_database_identity(self):
        self.assertIs(self.repo_a.check_ready(), True)
        self.assertTrue(self.repo_a.database_identity.startswith("postgres-endpoint:"))
        self.assertEqual(self.repo_a.database_identity, self.repo_b.database_identity)

    def test_typed_adapter_preserves_canonical_identity_across_create_and_replay(self):
        import hashlib
        import json
        from automation.agent_plan_repository import AgentPlanRecord, PlanSubtaskRecord
        from automation.postgres_agent_plan_adapter import PostgresAgentPlanRepositoryAdapter

        adapter = PostgresAgentPlanRepositoryAdapter(self.repo_a)
        client_key = "adapter-" + uuid.uuid4().hex
        scope_material = json.dumps(
            [self.tenant, self.owner, client_key],
            separators=(",", ":"), ensure_ascii=False,
        )
        scoped_key = "tenant:" + hashlib.sha256(scope_material.encode("utf-8")).hexdigest()
        subtasks = (PlanSubtaskRecord(
            subtask_id="research", agent_id="researcher", action="summarize",
            description="Typed adapter integration", required_capabilities=("research",),
            required_permissions=("web:read",), depends_on=(), requires_approval=True,
        ),)
        digest = agent_plan_request_digest(
            tenant_id=self.tenant, owner_principal_id=self.owner,
            subtasks=[{
                "subtask_id": "research", "agent_id": "researcher",
                "action": "summarize", "description": "Typed adapter integration",
                "required_capabilities": ["research"],
                "required_permissions": ["web:read"], "depends_on": [],
                "requires_approval": True,
            }],
        )
        proposed = AgentPlanRecord(
            plan_id="legacy-proposed-id", idempotency_key=scoped_key,
            request_digest=digest, created_at="2026-11-25T00:00:00+00:00",
            tenant_id=self.tenant, owner_principal_id=self.owner,
            client_idempotency_key=client_key, subtasks=subtasks,
        )
        created = adapter.create_plan(proposed)
        replay = adapter.create_plan(proposed)
        self.assertTrue(created.plan_id.startswith("ap_"))
        self.assertNotEqual("legacy-proposed-id", created.plan_id)
        self.assertEqual(created.plan_id, replay.plan_id)
        loaded = adapter.get_plan(
            plan_id=created.plan_id, tenant_id=self.tenant,
            owner_principal_id=self.owner,
        )
        self.assertEqual(created, loaded)
        self.assertIsNone(adapter.get_plan(
            plan_id=created.plan_id, tenant_id=self.tenant,
            owner_principal_id="different-owner",
        ))

    def test_typed_adapter_preserves_postgres_dispatch_expiry_and_fence(self):
        from automation.postgres_agent_plan_adapter import PostgresAgentPlanRepositoryAdapter

        adapter = PostgresAgentPlanRepositoryAdapter(self.repo_a)
        reservation = adapter.reserve_dispatch(
            plan_id=self.plan["plan"]["plan_id"], subtask_id="research",
            tenant_id=self.tenant, owner_principal_id=self.owner,
        )
        self.assertIsNotNone(reservation)
        self.assertEqual("reserved", reservation.state)
        self.assertTrue(reservation.claim_token)
        self.assertEqual(1, reservation.generation)
        self.assertTrue(reservation.expires_at)

    def test_crash_after_task_enqueue_recovers_by_stable_idempotency_key(self):
        """A new reservation generation must replay the one task created before a crash."""
        from automation.postgres_workflow_store import PostgresWorkflowRunStore
        from automation.task_runtime import TaskRuntime
        from core.authorization import AuthorizationDecision, Principal
        from core.authorization_gateway import AuthorizationGateway
        from automation.postgres_agent_plan_repository import PostgresAgentPlanRepository

        class Policy:
            def authorize(self, request):
                return AuthorizationDecision(True, "recovery-decision", "recovery-policy", "allowed")

        gateway = AuthorizationGateway(Policy(), audit=lambda request, decision: True)
        workflow_store = PostgresWorkflowRunStore(POSTGRES_URL, max_pool_size=2)
        runtime = TaskRuntime(
            workflow_store,
            authorization_gateway=gateway,
            principal_for_actor=lambda actor: Principal(
                actor, "human", tenant_id=self.tenant,
            ),
            policy_revision="recovery-policy",
        )
        plan_id = self.plan["plan"]["plan_id"]
        stable_key = f"agent-plan:{self.key}:research"
        try:
            first = self.repo_a.reserve_dispatch(
                tenant_id=self.tenant, owner_principal_id=self.owner,
                plan_id=plan_id, subtask_id="research",
            )
            self.assertIsNotNone(first)
            first_task = runtime.enqueue(
                actor_id=self.owner, idempotency_key=stable_key,
                name="agent-research-research",
                steps=[{"name": "research", "action": "emit", "argument": "recover",
                        "requires_approval": False}],
            )
            # Simulate process death: TaskRuntime committed, plan binding did not.
            with psycopg.connect(POSTGRES_URL) as conn:
                conn.execute(
                    "UPDATE agent_plan_dispatch_reservations SET expires_at=clock_timestamp()-interval '1 second' "
                    "WHERE tenant_id=%s AND owner_principal_id=%s AND plan_id=%s AND subtask_id='research'",
                    (self.tenant, self.owner, plan_id),
                )
            second = self.repo_b.reserve_dispatch(
                tenant_id=self.tenant, owner_principal_id=self.owner,
                plan_id=plan_id, subtask_id="research",
            )
            self.assertIsNotNone(second)
            self.assertGreater(second.generation, first.generation)
            replayed_task = runtime.enqueue(
                actor_id=self.owner, idempotency_key=stable_key,
                name="agent-research-research",
                steps=[{"name": "research", "action": "emit", "argument": "recover",
                        "requires_approval": False}],
            )
            self.assertEqual(first_task.task_id, replayed_task.task_id)
            self.assertTrue(self.repo_b.complete_dispatch(
                tenant_id=self.tenant, owner_principal_id=self.owner,
                plan_id=plan_id, subtask_id="research",
                claim_token=second.reservation_token, generation=second.generation,
                task_id=replayed_task.task_id, status=replayed_task.state,
            ))
            loaded = self.repo_b.list_subtasks(
                tenant_id=self.tenant, owner_principal_id=self.owner, plan_id=plan_id,
            )
            self.assertEqual(replayed_task.task_id, loaded[0].task_id)
            self.assertEqual("queued", loaded[0].status)
            with psycopg.connect(POSTGRES_URL) as conn:
                count = conn.execute(
                    "SELECT count(*) FROM workflow_runs WHERE idempotency_key=%s",
                    (stable_key,),
                ).fetchone()[0]
            self.assertEqual(1, count)
        finally:
            with psycopg.connect(POSTGRES_URL) as conn:
                conn.execute("DELETE FROM workflow_runs WHERE idempotency_key=%s", (stable_key,))
            workflow_store.close()

    def test_schema_readiness_checks_all_required_objects(self):
        self.repo_a.check_ready()

    def test_schema_readiness_fails_closed_when_metadata_is_missing(self):
        class EmptyCursor:
            def fetchall(self):
                return []

        class EmptyConnection:
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False
            def execute(self, *_args):
                return EmptyCursor()
        class EmptyPool:
            def connection(self):
                return EmptyConnection()

        repository = self.repository_type.__new__(self.repository_type)
        repository.pool = EmptyPool()
        with self.assertRaisesRegex(RuntimeError, "agent_plan_repository_schema_incomplete"):
            repository.check_ready()

    def test_plan_idempotency_is_owner_scoped_and_payload_bound(self):
        same = self.repo_b.create_plan(
            tenant_id=self.tenant, owner_principal_id=self.owner,
            client_idempotency_key=self.key, request_digest=self.digest,
            subtasks=[{"subtask_id": "research", "agent_id": "researcher",
                       "action": "summarize", "description": "Gather bounded evidence", "capabilities": ["research"],
                       "permissions": ["web:read"], "dependencies": [],
                       "requires_approval": True}],
        )
        self.assertEqual(same["plan"]["plan_id"], self.plan["plan"]["plan_id"])
        self.assertEqual("summarize", same["subtasks"][0]["action"])
        self.assertIsNone(self.repo_b.get_plan(
            tenant_id=self.tenant, owner_principal_id="human-other",
            plan_id=self.plan["plan"]["plan_id"],
        ))
        self.assertIsNone(self.repo_b.get_plan(
            tenant_id=self.tenant, owner_principal_id=self.owner,
            plan_id="ap_" + uuid.uuid4().hex,
        ))
        with self.assertRaises(Exception):
            self.repo_b.create_plan(
                tenant_id=self.tenant, owner_principal_id=self.owner,
                client_idempotency_key=self.key,
                request_digest="0" * 64,
                subtasks=[{"subtask_id": "research", "agent_id": "researcher",
                           "description": "Different payload"}],
            )

    def test_subtask_lifecycle_reads_and_updates_match_sqlite_contract(self):
        plan_id = self.plan["plan"]["plan_id"]
        records = self.repo_a.list_subtasks(
            tenant_id=self.tenant, owner_principal_id=self.owner, plan_id=plan_id,
        )
        self.assertEqual(1, len(records))
        self.assertEqual("research", records[0].subtask_id)
        self.assertEqual("summarize", records[0].action)
        self.assertIsNone(self.repo_a.list_subtasks(
            tenant_id=self.tenant, owner_principal_id="human-other", plan_id=plan_id,
        ))
        self.assertIsNone(self.repo_a.list_subtasks(
            tenant_id=self.tenant, owner_principal_id=self.owner, plan_id="ap_" + uuid.uuid4().hex,
        ))
        self.assertFalse(self.repo_a.update_subtask(
            tenant_id=self.tenant, owner_principal_id="human-other", plan_id=plan_id,
            subtask_id="research", status="queued",
        ))
        self.assertFalse(self.repo_a.update_subtask(
            tenant_id=self.tenant, owner_principal_id=self.owner, plan_id=plan_id,
            subtask_id="research", status="queued", expected_statuses=("succeeded",),
        ))
        self.assertTrue(self.repo_a.update_subtask(
            tenant_id=self.tenant, owner_principal_id=self.owner, plan_id=plan_id,
            subtask_id="research", status="queued", task_id="task-123",
            expected_statuses=("pending",),
        ))
        updated = self.repo_a.list_subtasks(
            tenant_id=self.tenant, owner_principal_id=self.owner, plan_id=plan_id,
        )
        self.assertEqual("task-123", updated[0].task_id)
        self.assertEqual("queued", updated[0].status)

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
        active = self._claim_outbox(self.repo_b, limit=20)
        active_for_plan = [row for row in active if row["plan_id"] == self.plan["plan"]["plan_id"]]
        self.assertEqual(len(active_for_plan), 1)
        self.assertEqual(active_for_plan[0]["dispatch_generation"], second.generation)
        self.assertEqual(active_for_plan[0]["reservation_token"], second.reservation_token)
        self.assertFalse(self.repo_a.mark_enqueued(
            tenant_id=self.tenant, owner_principal_id=self.owner,
            plan_id=self.plan["plan"]["plan_id"], subtask_id="research",
            reservation_token=first.reservation_token, generation=first.generation,
            claim_token="stale-claim-token-placeholder-000000", claim_generation=1,
            task_id="task-old",
        ))

    def test_outbox_claim_is_exclusive_and_stale_processing_is_recoverable(self):
        reservation = self.repo_a.reserve_dispatch(
            tenant_id=self.tenant, owner_principal_id=self.owner,
            plan_id=self.plan["plan"]["plan_id"], subtask_id="research",
        )
        claimed_a = self._claim_outbox(self.repo_a, limit=10)
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
        old_claim = target_a[0]
        recovered = self.repo_b.recover_stale_outbox(stale_after_seconds=60)
        self.assertEqual(recovered["requeued"], 1)
        with psycopg.connect(POSTGRES_URL) as conn:
            retry = conn.execute(
                "SELECT available_at > clock_timestamp() AS delayed FROM agent_plan_dispatch_outbox WHERE outbox_id=%s",
                (outbox_id,),
            ).fetchone()
            self.assertTrue(retry["delayed"])
            # Advance the retry schedule explicitly; production workers wait until available_at.
            conn.execute(
                "UPDATE agent_plan_dispatch_outbox SET available_at=clock_timestamp() WHERE outbox_id=%s",
                (outbox_id,),
            )
        reclaimed = self.repo_b.claim_outbox(limit=10)
        current_claim = next(row for row in reclaimed if row["outbox_id"] == outbox_id)
        self.assertNotEqual(old_claim["claim_token"], current_claim["claim_token"])
        self.assertGreater(current_claim["claim_generation"], old_claim["claim_generation"])
        from automation.postgres_agent_plan_repository import AgentPlanRepositoryConflict
        with self.assertRaisesRegex(AgentPlanRepositoryConflict, "claim_fenced"):
            self.repo_a.mark_enqueued(
                tenant_id=self.tenant, owner_principal_id=self.owner,
                plan_id=self.plan["plan"]["plan_id"], subtask_id="research",
                reservation_token=reservation.reservation_token, generation=reservation.generation,
                claim_token=old_claim["claim_token"], claim_generation=old_claim["claim_generation"],
                task_id="stale-task",
            )
        self.assertTrue(self.repo_b.mark_enqueued(
            tenant_id=self.tenant, owner_principal_id=self.owner,
            plan_id=self.plan["plan"]["plan_id"], subtask_id="research",
            reservation_token=reservation.reservation_token, generation=reservation.generation,
            claim_token=current_claim["claim_token"], claim_generation=current_claim["claim_generation"],
            task_id="current-task",
        ))


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
        claimed = self.repo_b.claim_outbox(limit=20)
        self.assertFalse(any(row["plan_id"] == self.plan["plan"]["plan_id"] for row in claimed))
        with psycopg.connect(POSTGRES_URL) as conn:
            state = conn.execute(
                "SELECT state FROM agent_plan_dispatch_outbox WHERE tenant_id=%s AND owner_principal_id=%s "
                "AND plan_id=%s AND dispatch_generation=%s",
                (self.tenant, self.owner, self.plan["plan"]["plan_id"], first.generation),
            ).fetchone()[0]
        self.assertEqual(state, "dead_letter")

    def test_dispatch_cancellation_persists_tombstone_and_fences_late_completion(self):
        plan_id = self.plan["plan"]["plan_id"]
        self.assertTrue(self.repo_a.cancel_dispatch(
            tenant_id=self.tenant, owner_principal_id=self.owner,
            plan_id=plan_id, subtask_id="research",
        ))
        # Dispatch fencing must not choose the task's terminal outcome. The
        # coordinator reconciles TaskRuntime and owns the final status update.
        subtasks = self.repo_b.list_subtasks(
            tenant_id=self.tenant, owner_principal_id=self.owner, plan_id=plan_id,
        )
        self.assertEqual("pending", subtasks[0].status)
        tombstone = self.repo_b.get_dispatch(
            tenant_id=self.tenant, owner_principal_id=self.owner,
            plan_id=plan_id, subtask_id="research",
        )
        self.assertEqual("cancelled", tombstone.state)
        self.assertIsNone(tombstone.claim_token)
        self.assertFalse(self.repo_b.complete_dispatch(
            tenant_id=self.tenant, owner_principal_id=self.owner,
            plan_id=plan_id, subtask_id="research",
            claim_token=tombstone.reservation_token, generation=tombstone.generation,
            task_id="late-task", status="queued",
        ))
        replay = self.repo_b.reserve_dispatch(
            tenant_id=self.tenant, owner_principal_id=self.owner,
            plan_id=plan_id, subtask_id="research",
        )
        self.assertEqual("cancelled", replay.state)
        self.assertIsNone(replay.task_id)

    def test_dispatch_reads_hide_foreign_owner(self):
        reservation = self.repo_a.reserve_dispatch(
            tenant_id=self.tenant, owner_principal_id=self.owner,
            plan_id=self.plan["plan"]["plan_id"], subtask_id="research",
        )
        owned = self.repo_a.get_dispatch(
            tenant_id=self.tenant, owner_principal_id=self.owner,
            plan_id=self.plan["plan"]["plan_id"], subtask_id="research",
        )
        self.assertEqual(reservation.reservation_token, owned.reservation_token)
        self.assertIsNone(self.repo_b.get_dispatch(
            tenant_id=self.tenant, owner_principal_id="human-other",
            plan_id=self.plan["plan"]["plan_id"], subtask_id="research",
        ))

    def test_mark_enqueued_rolls_back_if_subtask_generation_changed(self):
        reservation = self.repo_a.reserve_dispatch(
            tenant_id=self.tenant, owner_principal_id=self.owner,
            plan_id=self.plan["plan"]["plan_id"], subtask_id="research",
        )
        claimed = self.repo_a.claim_outbox(limit=20)
        claim = next(row for row in claimed if row["plan_id"] == self.plan["plan"]["plan_id"])
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
                generation=reservation.generation,
                claim_token=claim["claim_token"],
                claim_generation=claim["claim_generation"],
                task_id="task-test",
            )
        with psycopg.connect(POSTGRES_URL) as conn:
            state = conn.execute(
                "SELECT state FROM agent_plan_dispatch_reservations WHERE tenant_id=%s AND owner_principal_id=%s "
                "AND plan_id=%s AND subtask_id='research'",
                (self.tenant, self.owner, self.plan["plan"]["plan_id"]),
            ).fetchone()[0]
        self.assertEqual(state, "reserved")


    def test_complete_dispatch_atomically_binds_task_and_delivers_outbox(self):
        plan_id = self.plan["plan"]["plan_id"]
        task_id = "task-" + uuid.uuid4().hex
        with psycopg.connect(POSTGRES_URL) as conn:
            conn.execute(
                """INSERT INTO workflow_runs
                   (run_id,idempotency_key,request_digest,workflow_name,definition_json,state,
                    tenant_id,owner_principal_id,ownership_status)
                   VALUES (%s,%s,%s,'agent-plan-test','{}'::jsonb,'queued',%s,%s,'owned')""",
                (task_id, "idem-" + uuid.uuid4().hex, "0" * 64, self.tenant, self.owner),
            )
        reservation = self.repo_a.reserve_dispatch(
            tenant_id=self.tenant, owner_principal_id=self.owner,
            plan_id=plan_id, subtask_id="research",
        )
        self.assertIsNotNone(reservation)
        self.assertTrue(self.repo_a.complete_dispatch(
            tenant_id=self.tenant, owner_principal_id=self.owner,
            plan_id=plan_id, subtask_id="research",
            claim_token=reservation.claim_token, generation=reservation.generation,
            task_id=task_id, status="queued",
        ))
        self.assertFalse(self.repo_b.complete_dispatch(
            tenant_id=self.tenant, owner_principal_id=self.owner,
            plan_id=plan_id, subtask_id="research",
            claim_token=reservation.claim_token, generation=reservation.generation,
            task_id=task_id, status="queued",
        ))
        dispatch = self.repo_b.get_dispatch(
            tenant_id=self.tenant, owner_principal_id=self.owner,
            plan_id=plan_id, subtask_id="research",
        )
        self.assertEqual("enqueued", dispatch.state)
        self.assertEqual(task_id, dispatch.task_id)
        subtask = self.repo_b.list_subtasks(
            tenant_id=self.tenant, owner_principal_id=self.owner, plan_id=plan_id,
        )[0]
        self.assertEqual(task_id, subtask.task_id)
        self.assertEqual("queued", subtask.status)
        with psycopg.connect(POSTGRES_URL) as conn:
            outbox_state = conn.execute(
                """SELECT state,task_id,claim_token FROM agent_plan_dispatch_outbox
                   WHERE tenant_id=%s AND owner_principal_id=%s AND plan_id=%s
                     AND subtask_id='research' AND dispatch_generation=%s""",
                (self.tenant, self.owner, plan_id, reservation.generation),
            ).fetchone()
        self.assertEqual(("delivered", task_id, None), tuple(outbox_state))


    def test_cancellation_after_enqueue_preserves_task_for_runtime_cancellation(self):
        plan_id = self.plan["plan"]["plan_id"]
        task_id = "task-" + uuid.uuid4().hex
        with psycopg.connect(POSTGRES_URL) as conn:
            conn.execute(
                """INSERT INTO workflow_runs
                   (run_id,idempotency_key,request_digest,workflow_name,definition_json,state,
                    tenant_id,owner_principal_id,ownership_status)
                   VALUES (%s,%s,%s,%s,'{}'::jsonb,'queued',%s,%s,'owned')""",
                (task_id, "idem-" + uuid.uuid4().hex, "0" * 64,
                 "agent-plan-test", self.tenant, self.owner),
            )
        reservation = self.repo_a.reserve_dispatch(
            tenant_id=self.tenant, owner_principal_id=self.owner,
            plan_id=plan_id, subtask_id="research",
        )
        self.assertTrue(self.repo_a.complete_dispatch(
            tenant_id=self.tenant, owner_principal_id=self.owner,
            plan_id=plan_id, subtask_id="research",
            claim_token=reservation.claim_token, generation=reservation.generation,
            task_id=task_id, status="queued",
        ))
        self.assertFalse(self.repo_b.cancel_dispatch(
            tenant_id=self.tenant, owner_principal_id=self.owner,
            plan_id=plan_id, subtask_id="research",
        ))
        dispatch = self.repo_b.get_dispatch(
            tenant_id=self.tenant, owner_principal_id=self.owner,
            plan_id=plan_id, subtask_id="research",
        )
        self.assertEqual("enqueued", dispatch.state)
        self.assertEqual(task_id, dispatch.task_id)
        subtask = self.repo_b.list_subtasks(
            tenant_id=self.tenant, owner_principal_id=self.owner, plan_id=plan_id,
        )[0]
        self.assertEqual("queued", subtask.status)
        self.assertEqual(task_id, subtask.task_id)


    def test_cancellation_does_not_overwrite_terminal_subtask_outcome(self):
        plan_id = self.plan["plan"]["plan_id"]
        self.assertTrue(self.repo_a.update_subtask(
            tenant_id=self.tenant, owner_principal_id=self.owner,
            plan_id=plan_id, subtask_id="research", status="succeeded",
        ))
        self.assertFalse(self.repo_b.cancel_dispatch(
            tenant_id=self.tenant, owner_principal_id=self.owner,
            plan_id=plan_id, subtask_id="research",
        ))
        subtask = self.repo_b.list_subtasks(
            tenant_id=self.tenant, owner_principal_id=self.owner, plan_id=plan_id,
        )[0]
        self.assertEqual("succeeded", subtask.status)
        self.assertIsNone(self.repo_b.get_dispatch(
            tenant_id=self.tenant, owner_principal_id=self.owner,
            plan_id=plan_id, subtask_id="research",
        ))


    def test_outbox_recovery_dead_letters_after_attempt_limit(self):
        reservation = self.repo_a.reserve_dispatch(
            tenant_id=self.tenant, owner_principal_id=self.owner,
            plan_id=self.plan["plan"]["plan_id"], subtask_id="research",
        )
        claimed = self.repo_a.claim_outbox(limit=10)
        target = next(row for row in claimed if row["plan_id"] == self.plan["plan"]["plan_id"])
        with psycopg.connect(POSTGRES_URL) as conn:
            conn.execute(
                "UPDATE agent_plan_dispatch_outbox SET updated_at=clock_timestamp()-interval '120 seconds' "
                "WHERE outbox_id=%s", (target["outbox_id"],),
            )
        recovered = self.repo_b.recover_stale_outbox(
            stale_after_seconds=60, max_attempts=1,
        )
        self.assertEqual({"requeued": 0, "dead_lettered": 1}, recovered)
        with psycopg.connect(POSTGRES_URL) as conn:
            row = conn.execute(
                "SELECT state,claim_token,failure_code FROM agent_plan_dispatch_outbox WHERE outbox_id=%s",
                (target["outbox_id"],),
            ).fetchone()
        self.assertEqual(("dead_letter", None, "retry_exhausted"), tuple(row))
        reservation_after = self.repo_b.get_dispatch(
            tenant_id=self.tenant, owner_principal_id=self.owner,
            plan_id=self.plan["plan"]["plan_id"], subtask_id="research",
        )
        self.assertEqual("abandoned", reservation_after.state)
        subtask_after = self.repo_b.list_subtasks(
            tenant_id=self.tenant, owner_principal_id=self.owner,
            plan_id=self.plan["plan"]["plan_id"],
        )
        self.assertEqual("failed", next(item.status for item in subtask_after if item.subtask_id == "research"))
        self.assertNotIn(
            target["outbox_id"],
            [item["outbox_id"] for item in self.repo_b.claim_outbox(limit=10)],
        )


    def test_outbox_heartbeat_requires_current_claim_and_reservation_fences(self):
        reservation = self.repo_a.reserve_dispatch(
            tenant_id=self.tenant, owner_principal_id=self.owner,
            plan_id=self.plan["plan"]["plan_id"], subtask_id="research",
        )
        claimed = self.repo_a.claim_outbox(limit=10)
        item = next(row for row in claimed if row["plan_id"] == self.plan["plan"]["plan_id"])
        args = {
            "tenant_id": self.tenant,
            "owner_principal_id": self.owner,
            "plan_id": self.plan["plan"]["plan_id"],
            "subtask_id": "research",
            "reservation_token": reservation.reservation_token,
            "generation": reservation.generation,
            "claim_token": item["claim_token"],
            "claim_generation": item["claim_generation"],
        }
        before_heartbeat = self.repo_a.get_dispatch(
            plan_id=args["plan_id"], subtask_id=args["subtask_id"],
            tenant_id=self.tenant, owner_principal_id=self.owner,
        )
        self.assertTrue(self.repo_a.heartbeat_outbox_claim(**args))
        after_heartbeat = self.repo_a.get_dispatch(
            plan_id=args["plan_id"], subtask_id=args["subtask_id"],
            tenant_id=self.tenant, owner_principal_id=self.owner,
        )
        self.assertGreater(after_heartbeat.expires_at, before_heartbeat.expires_at)

        with psycopg.connect(POSTGRES_URL) as conn:
            conn.execute(
                "UPDATE agent_plan_dispatch_outbox SET updated_at=clock_timestamp()-interval '120 seconds' "
                "WHERE outbox_id=%s", (item["outbox_id"],),
            )
        recovered = self.repo_b.recover_stale_outbox(stale_after_seconds=60)
        self.assertEqual(1, recovered["requeued"])
        self.assertFalse(self.repo_a.heartbeat_outbox_claim(**args))
        with psycopg.connect(POSTGRES_URL) as conn:
            conn.execute(
                "UPDATE agent_plan_dispatch_outbox SET available_at=clock_timestamp() WHERE outbox_id=%s",
                (item["outbox_id"],),
            )
        reclaimed = self.repo_b.claim_outbox(limit=10)
        current = next(row for row in reclaimed if row["outbox_id"] == item["outbox_id"])
        current_args = dict(args)
        current_args.update({
            "claim_token": current["claim_token"],
            "claim_generation": current["claim_generation"],
        })
        self.assertTrue(self.repo_b.heartbeat_outbox_claim(**current_args))
        self.assertFalse(self.repo_a.heartbeat_outbox_claim(**args))

    def test_completion_reconciles_cancelled_goal_without_releasing_held_task(self):
        from automation.postgres_goal_store import PostgresWorkflowGoalStore

        plan_id = self.plan["plan"]["plan_id"]
        reservation = self.repo_a.reserve_dispatch(
            tenant_id=self.tenant, owner_principal_id=self.owner,
            plan_id=plan_id, subtask_id="research",
        )
        claim = next(
            row for row in self._claim_outbox(self.repo_a, limit=20)
            if row["plan_id"] == plan_id
        )
        task_id = self._create_held_task("cancelled-goal-race")
        goal_id = "goal-" + uuid.uuid4().hex
        goals = PostgresWorkflowGoalStore(POSTGRES_URL, min_pool_size=1, max_pool_size=2)
        try:
            goals.create_goal(
                tenant_id=self.tenant, owner_principal_id=self.owner, goal_id=goal_id,
            )
            self.assertTrue(goals.attach_task(
                tenant_id=self.tenant, owner_principal_id=self.owner,
                goal_id=goal_id, task_id=task_id,
            ))
            goals.cancel_goal(
                tenant_id=self.tenant, owner_principal_id=self.owner,
                goal_id=goal_id, actor_id=self.owner,
            )
        finally:
            goals.close()

        self.assertTrue(self.repo_b.complete_claimed_dispatch(
            tenant_id=self.tenant, owner_principal_id=self.owner,
            plan_id=plan_id, subtask_id="research",
            reservation_token=reservation.reservation_token,
            dispatch_generation=reservation.generation,
            outbox_claim_token=claim["claim_token"],
            outbox_claim_generation=claim["claim_generation"],
            task_id=task_id, status="queued",
        ))
        with psycopg.connect(POSTGRES_URL) as conn:
            task = conn.execute(
                "SELECT state,dispatch_ready FROM workflow_runs WHERE run_id=%s",
                (task_id,),
            ).fetchone()
            outbox = conn.execute(
                """SELECT state,failure_code,claim_token FROM agent_plan_dispatch_outbox
                   WHERE tenant_id=%s AND owner_principal_id=%s AND plan_id=%s AND subtask_id='research'""",
                (self.tenant, self.owner, plan_id),
            ).fetchone()
            reservation_state = conn.execute(
                """SELECT state FROM agent_plan_dispatch_reservations
                   WHERE tenant_id=%s AND owner_principal_id=%s AND plan_id=%s AND subtask_id='research'""",
                (self.tenant, self.owner, plan_id),
            ).fetchone()[0]
            subtask_state = conn.execute(
                """SELECT status FROM agent_plan_subtasks
                   WHERE tenant_id=%s AND owner_principal_id=%s AND plan_id=%s AND subtask_id='research'""",
                (self.tenant, self.owner, plan_id),
            ).fetchone()[0]
            event = conn.execute(
                "SELECT kind FROM task_events WHERE task_id=%s ORDER BY event_id DESC LIMIT 1",
                (task_id,),
            ).fetchone()
        self.assertEqual(("cancelled", False), tuple(task))
        self.assertEqual(("dead_letter", "goal_cancelled", None), tuple(outbox))
        self.assertEqual("cancelled", reservation_state)
        self.assertEqual("cancelled", subtask_state)
        self.assertEqual("task.cancelled", event[0])
