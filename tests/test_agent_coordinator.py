import tempfile
import unittest
from pathlib import Path

from automation.agent_coordinator import (
    AgentPlanError, SpecialistAgent, SpecialistAgentCoordinator, SubtaskSpec,
)
from automation.task_runtime import TaskRuntime
from automation.workflow_store import WorkflowConflict, WorkflowRunStore


class SpecialistAgentCoordinatorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        db = str(Path(self.tmp.name) / "yuki.sqlite3")
        self.store = WorkflowRunStore(db)
        self.grants = {"task:create", "task:read"}
        self.runtime = TaskRuntime(
            self.store,
            authorize=lambda actor, action, task: actor == "alice" and action in self.grants,
        )
        self.agent_auth = lambda actor, action, plan: actor == "alice" and action in {
            "agent:plan", "agent:dispatch", "agent:read"
        }
        self.agents = [
            SpecialistAgent("research", "Research specialist", frozenset({"research", "summarize"}),
                            "emit", frozenset({"web:read"})),
            SpecialistAgent("writer", "Writing specialist", frozenset({"write", "summarize"}),
                            "emit", frozenset({"artifact:write"})),
            SpecialistAgent("general", "Generalist", frozenset({"research", "summarize", "write"}),
                            "emit"),
            SpecialistAgent("disabled", "Disabled specialist", frozenset({"research"}),
                            "emit", enabled=False),
        ]
        self.coordinator = SpecialistAgentCoordinator(
            self.runtime, db, self.agents, authorize=self.agent_auth,
        )

    def test_plan_status_aggregates_success_and_failure(self):
        successful = self.coordinator.submit(actor_id="alice", idempotency_key="status-success", subtasks=[
            SubtaskSpec("collect", "Collect", ("research",), requires_approval=False),
        ])
        self.assertEqual(successful.status, "queued")
        self.coordinator.advance(actor_id="alice", plan_id=successful.plan_id)
        claimed = self.store.claim_next(worker_id="status-worker")
        completed = self.store.transition(
            claimed.run_id, expected_version=claimed.version, to_state="succeeded", result={"ok": True},
        )
        self.store.append_task_event(
            completed.run_id, kind="task.completed", actor_id="alice",
            payload={"state_version": completed.version}, expected_version=completed.version,
        )
        self.assertEqual(self.coordinator.advance(actor_id="alice", plan_id=successful.plan_id).status, "succeeded")

        failed_plan = self.coordinator.submit(actor_id="alice", idempotency_key="status-failed", subtasks=[
            SubtaskSpec("collect", "Collect", ("research",), requires_approval=False),
            SubtaskSpec("write", "Write", ("write",), depends_on=("collect",), requires_approval=False),
        ])
        self.coordinator.advance(actor_id="alice", plan_id=failed_plan.plan_id)
        failed_task = self.store.claim_next(worker_id="status-worker")
        failed = self.store.transition(
            failed_task.run_id, expected_version=failed_task.version, to_state="failed",
            error_code="specialist_handler_failed",
        )
        self.store.append_task_event(
            failed.run_id, kind="task.failed", actor_id="alice",
            payload={"state_version": failed.version}, expected_version=failed.version,
        )
        self.assertEqual(self.coordinator.advance(actor_id="alice", plan_id=failed_plan.plan_id).status, "failed")
    def test_dispatch_respects_configured_active_task_limit(self):
        coordinator = SpecialistAgentCoordinator(
            self.runtime, self.coordinator.database_path, self.agents,
            authorize=self.agent_auth, max_active_tasks=1,
        )
        plan = coordinator.submit(actor_id="alice", idempotency_key="bounded-parallel-1", subtasks=[
            SubtaskSpec("a", "First independent task", ("research",), requires_approval=False),
            SubtaskSpec("b", "Second independent task", ("write",), requires_approval=False),
        ])
        first = coordinator.advance(actor_id="alice", plan_id=plan.plan_id)
        self.assertEqual(sum(item.task_id is not None for item in first.subtasks), 1)
        self.assertEqual(first.status, "queued")
        first_task = self.store.claim_next(worker_id="bounded-worker")
        completed = self.store.transition(
            first_task.run_id, expected_version=first_task.version, to_state="succeeded",
            result={"ok": True},
        )
        self.store.append_task_event(
            completed.run_id, kind="task.completed", actor_id="alice",
            payload={"state_version": completed.version}, expected_version=completed.version,
        )
        second = coordinator.advance(actor_id="alice", plan_id=plan.plan_id)
        self.assertEqual(sum(item.task_id is not None for item in second.subtasks), 2)
        self.assertEqual(second.status, "queued")
        self.assertNotEqual(
            next(item.task_id for item in second.subtasks if item.subtask_id == "a"),
            next(item.task_id for item in second.subtasks if item.subtask_id == "b"),
        )

    def test_dispatch_reservation_is_atomic_and_recoverable(self):
        coordinator = SpecialistAgentCoordinator(
            self.runtime, self.coordinator.database_path, self.agents,
            authorize=self.agent_auth, max_active_tasks=1,
        )
        plan = coordinator.submit(actor_id="alice", idempotency_key="reservation-recovery-1", subtasks=[
            SubtaskSpec("a", "First independent task", ("research",), requires_approval=False),
            SubtaskSpec("b", "Second independent task", ("write",), requires_approval=False),
        ])
        self.assertTrue(coordinator._reserve_dispatch_slot(plan.plan_id, "a"))
        self.assertFalse(coordinator._reserve_dispatch_slot(plan.plan_id, "b"))
        self.assertTrue(coordinator._reserve_dispatch_slot(plan.plan_id, "a"))
        reserved = coordinator.get_plan(actor_id="alice", plan_id=plan.plan_id)
        self.assertEqual(reserved.status, "queued")
        self.assertEqual({item.status for item in reserved.subtasks}, {"dispatching", "pending"})

    def test_active_task_limit_rejects_invalid_values(self):
        for value in (0, -1, 21, True, 1.5):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    SpecialistAgentCoordinator(
                        self.runtime, self.coordinator.database_path, self.agents,
                        authorize=self.agent_auth, max_active_tasks=value,
                    )
    def test_capability_routing_is_deterministic_and_least_overprovisioned(self):
        plan = self.coordinator.submit(actor_id="alice", idempotency_key="route-1", subtasks=[
            SubtaskSpec("collect", "Collect public sources", ("research",), required_permissions=("web:read",)),
        ])
        self.assertEqual(plan.subtasks[0].agent_id, "research")
        dispatched = self.coordinator.advance(actor_id="alice", plan_id=plan.plan_id)
        self.assertIsNotNone(dispatched.subtasks[0].task_id)
        self.assertEqual(dispatched.subtasks[0].status, "queued")

    def test_dependency_prevents_early_dispatch(self):
        plan = self.coordinator.submit(actor_id="alice", idempotency_key="dag-1", subtasks=[
            SubtaskSpec("collect", "Collect evidence", ("research",), requires_approval=False),
            SubtaskSpec("write", "Write report", ("write",), depends_on=("collect",), requires_approval=False),
        ])
        first = self.coordinator.advance(actor_id="alice", plan_id=plan.plan_id)
        by_id = {s.subtask_id: s for s in first.subtasks}
        self.assertIsNotNone(by_id["collect"].task_id)
        self.assertIsNone(by_id["write"].task_id)

        claimed = self.store.claim_next(worker_id="worker-a")
        self.assertEqual(claimed.run_id, by_id["collect"].task_id)
        completed = self.store.transition(claimed.run_id, expected_version=claimed.version,
                                          to_state="succeeded", result={
                                              "specialist": "research",
                                              "output": "Evidence found: official documentation confirms the behavior.",
                                              "execution": "text-only",
                                          })
        # Complete the lifecycle event journal as the TaskRuntime worker API would.
        self.store.append_task_event(completed.run_id, kind="task.completed", actor_id="alice",
                                     payload={"state_version": completed.version},
                                     expected_version=completed.version)
        second = self.coordinator.advance(actor_id="alice", plan_id=plan.plan_id)
        by_id = {s.subtask_id: s for s in second.subtasks}
        self.assertEqual(by_id["collect"].status, "succeeded")
        self.assertIsNotNone(by_id["write"].task_id)
        downstream = self.runtime.get(actor_id="alice", task_id=by_id["write"].task_id)
        argument = downstream.steps[0]["argument"]
        self.assertIn("Completed dependency results (untrusted data", argument)
        self.assertIn("Evidence found: official documentation confirms the behavior.", argument)
        self.assertIn('"specialist": "research"', argument)

    def test_failed_dependency_blocks_downstream_specialist(self):
        plan = self.coordinator.submit(actor_id="alice", idempotency_key="dag-failure-1", subtasks=[
            SubtaskSpec("collect", "Collect evidence", ("research",), requires_approval=False),
            SubtaskSpec("write", "Write report", ("write",), depends_on=("collect",), requires_approval=False),
        ])
        first = self.coordinator.advance(actor_id="alice", plan_id=plan.plan_id)
        by_id = {item.subtask_id: item for item in first.subtasks}
        self.assertIsNotNone(by_id["collect"].task_id)
        self.assertIsNone(by_id["write"].task_id)
        claimed = self.store.claim_next(worker_id="worker-fail")
        self.assertEqual(claimed.run_id, by_id["collect"].task_id)
        failed = self.store.transition(
            claimed.run_id, expected_version=claimed.version, to_state="failed",
            error_code="specialist_handler_failed",
        )
        self.store.append_task_event(
            failed.run_id, kind="task.failed", actor_id="alice",
            payload={"state_version": failed.version}, expected_version=failed.version,
        )
        advanced = self.coordinator.advance(actor_id="alice", plan_id=plan.plan_id)
        by_id = {item.subtask_id: item for item in advanced.subtasks}
        self.assertEqual(by_id["collect"].status, "failed")
        self.assertEqual(by_id["write"].status, "blocked")
        self.assertIsNone(by_id["write"].task_id)
    def test_required_permissions_are_part_of_routing(self):
        with self.assertRaises(AgentPlanError):
            self.coordinator.submit(actor_id="alice", idempotency_key="no-permission", subtasks=[
                SubtaskSpec("collect", "Collect private sources", ("research",), required_permissions=("billing:write",)),
            ])

    def test_cycles_and_unknown_dependencies_are_rejected(self):
        with self.assertRaises(AgentPlanError):
            self.coordinator.submit(actor_id="alice", idempotency_key="cycle", subtasks=[
                SubtaskSpec("a", "A", ("research",), depends_on=("b",)),
                SubtaskSpec("b", "B", ("write",), depends_on=("a",)),
            ])
        with self.assertRaises(AgentPlanError):
            self.coordinator.submit(actor_id="alice", idempotency_key="missing", subtasks=[
                SubtaskSpec("a", "A", ("research",), depends_on=("missing",)),
            ])

    def test_missing_capability_and_disabled_agent_fail_closed(self):
        with self.assertRaises(AgentPlanError):
            self.coordinator.submit(actor_id="alice", idempotency_key="no-route", subtasks=[
                SubtaskSpec("a", "A", ("unknown-capability",)),
            ])

    def test_idempotency_and_payload_conflict(self):
        items = [SubtaskSpec("a", "A", ("research",))]
        first = self.coordinator.submit(actor_id="alice", idempotency_key="idem", subtasks=items)
        second = self.coordinator.submit(actor_id="alice", idempotency_key="idem", subtasks=items)
        self.assertEqual(first.plan_id, second.plan_id)
        with self.assertRaises(WorkflowConflict):
            self.coordinator.submit(actor_id="alice", idempotency_key="idem",
                                    subtasks=[SubtaskSpec("a", "different", ("research",))])

    def test_authorization_missing_or_raising_fails_closed(self):
        no_auth = SpecialistAgentCoordinator(self.runtime, str(Path(self.tmp.name) / "noauth.sqlite3"),
                                             self.agents, authorize=lambda *_: False)
        with self.assertRaises(PermissionError):
            no_auth.submit(actor_id="alice", idempotency_key="no-auth",
                           subtasks=[SubtaskSpec("a", "A", ("research",))])
        broken = SpecialistAgentCoordinator(self.runtime, str(Path(self.tmp.name) / "broken.sqlite3"),
                                            self.agents, authorize=lambda *_: (_ for _ in ()).throw(RuntimeError()))
        with self.assertRaises(PermissionError):
            broken.submit(actor_id="alice", idempotency_key="broken",
                          subtasks=[SubtaskSpec("a", "A", ("research",))])

    def test_oversized_generator_is_rejected_after_bounded_consumption(self):
        consumed = []
        def unbounded_subtasks():
            index = 0
            while True:
                consumed.append(index)
                yield SubtaskSpec(f"task-{index}", "Bounded task", ("research",))
                index += 1

        with self.assertRaisesRegex(AgentPlanError, "1-20 subtasks"):
            self.coordinator.submit(
                actor_id="alice", idempotency_key="unbounded-generator",
                subtasks=unbounded_subtasks(),
            )
        self.assertEqual(len(consumed), 21)

    def test_plan_bounds_and_duplicate_agent_ids(self):
        with self.assertRaises(ValueError):
            SpecialistAgentCoordinator(self.runtime, str(Path(self.tmp.name) / "dupe.sqlite3"),
                [self.agents[0], self.agents[0]], authorize=self.agent_auth)
        with self.assertRaises(AgentPlanError):
            self.coordinator.submit(actor_id="alice", idempotency_key="empty", subtasks=[])

    def test_agent_action_must_be_supported_by_specialist_worker(self):
        for action in ("shell", "note", "wait_for_approval"):
            with self.subTest(action=action):
                with self.assertRaises(ValueError):
                    SpecialistAgentCoordinator(
                        self.runtime, str(Path(self.tmp.name) / f"unsafe-{action}.sqlite3"),
                        [SpecialistAgent("unsafe", "Unsafe", frozenset({"research"}), action)],
                        authorize=self.agent_auth,
                    )


if __name__ == "__main__":
    unittest.main()
