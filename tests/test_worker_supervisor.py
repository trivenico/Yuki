import tempfile
import unittest
from threading import Barrier, Lock
from pathlib import Path

from automation.agent_coordinator import SpecialistAgent, SpecialistAgentCoordinator, SubtaskSpec
from automation.specialist_worker import SpecialistTaskWorker
from automation.task_runtime import TaskRuntime
from automation.workflow_store import WorkflowRunStore
from automation.worker_supervisor import WorkerSupervisor


class WorkerSupervisorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        db = str(Path(self.tmp.name) / "supervisor.sqlite3")
        self.store = WorkflowRunStore(db)
        grants = {"task:create", "task:read", "task:progress", "task:artifact",
                  "task:complete", "task:fail", "task:cancel"}
        self.runtime = TaskRuntime(
            self.store,
            authorize=lambda actor, action, _task: (
                actor == "alice" and action in grants
            ) or (actor == "worker-service" and action in grants | {"task:recover"}),
        )
        self.coordinator = SpecialistAgentCoordinator(
            self.runtime, db,
            [SpecialistAgent("report-agent", "Reporter", frozenset({"reporting"}), "emit")],
            authorize=lambda actor, action, _plan: actor == "alice" and action in {
                "agent:plan", "agent:dispatch", "agent:read"
            },
        )
        self.worker = SpecialistTaskWorker(
            self.runtime, worker_id="worker-1", service_actor_id="worker-service",
            handlers={"report-agent": lambda request: "Report: " + request},
        )

    def create_task(self, key):
        plan = self.coordinator.submit(
            actor_id="alice", idempotency_key=key,
            subtasks=[SubtaskSpec("report", "Summarize verified inputs", ("reporting",),
                                  requires_approval=False)],
        )
        plan = self.coordinator.advance(actor_id="alice", plan_id=plan.plan_id)
        return plan.subtasks[0].task_id

    def test_cycle_processes_only_configured_batch(self):
        first = self.create_task("first")
        second = self.create_task("second")
        supervisor = WorkerSupervisor(self.runtime, self.worker, max_tasks_per_cycle=1)
        one = supervisor.run_cycle()
        self.assertEqual(one.processed_count, 1)
        self.assertEqual(one.succeeded_count, 1)
        self.assertEqual(self.store.get(first).state, "succeeded")
        self.assertEqual(self.store.get(second).state, "queued")
        two = supervisor.run_cycle()
        self.assertEqual(two.processed_count, 1)
        self.assertEqual(two.succeeded_count, 1)
        self.assertEqual(self.store.get(second).state, "succeeded")

    def test_parallel_cycle_runs_independent_tasks_concurrently(self):
        self.create_task("parallel-a")
        self.create_task("parallel-b")
        rendezvous = Barrier(2, timeout=5)
        lock = Lock()
        active = 0
        peak = 0

        def concurrent_handler(request):
            nonlocal active, peak
            with lock:
                active += 1
                peak = max(peak, active)
            try:
                rendezvous.wait()
                return "Report: " + request
            finally:
                with lock:
                    active -= 1

        worker = SpecialistTaskWorker(
            self.runtime, worker_id="parallel-worker",
            service_actor_id="worker-service",
            handlers={"report-agent": concurrent_handler},
        )
        report = WorkerSupervisor(
            self.runtime, worker, max_tasks_per_cycle=2, max_workers=2,
        ).run_cycle()
        self.assertEqual(report.processed_count, 2)
        self.assertEqual(report.succeeded_count, 2)
        self.assertEqual(peak, 2)

    def test_cycle_recovers_expired_leases_without_replay(self):
        task_id = self.create_task("expired")
        claimed = self.store.claim_next(worker_id="crashed-worker", lease_seconds=60)
        self.assertEqual(claimed.run_id, task_id)
        with self.store._connect() as db:
            db.execute("UPDATE workflow_runs SET lease_expires_at='2000-01-01T00:00:00+00:00' WHERE run_id=?",
                       (task_id,))
        report = WorkerSupervisor(self.runtime, self.worker).run_cycle()
        self.assertEqual(report.recovered_task_ids, (task_id,))
        self.assertEqual(report.processed_count, 0)
        self.assertEqual(self.store.get(task_id).state, "interrupted")
        self.assertEqual(self.runtime.events(actor_id="alice", task_id=task_id)[-1].kind,
                         "task.interrupted")

    def test_supervisor_configuration_is_bounded(self):
        with self.assertRaises(ValueError):
            WorkerSupervisor(self.runtime, self.worker, max_tasks_per_cycle=0)
        with self.assertRaises(ValueError):
            WorkerSupervisor(self.runtime, self.worker, recovery_limit=101)
        with self.assertRaises(ValueError):
            WorkerSupervisor(self.runtime, self.worker, max_workers=17)
        other = TaskRuntime(self.store, authorize=lambda *_: True)
        with self.assertRaises(ValueError):
            WorkerSupervisor(other, self.worker)

    def test_empty_cycle_returns_idle(self):
        report = WorkerSupervisor(self.runtime, self.worker).run_cycle()
        self.assertTrue(report.idle)
        self.assertEqual(report.processed_count, 0)


if __name__ == "__main__":
    unittest.main()
