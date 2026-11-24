import tempfile
import unittest
from pathlib import Path

from automation.agent_coordinator import SpecialistAgent, SpecialistAgentCoordinator, SubtaskSpec
from automation.task_runtime import TaskRuntime
from automation.workflow_store import WorkflowRunStore
from core.authorization import AuthorizationDecision, Principal
from core.authorization_gateway import AuthorizationGateway
from core.delegation_authority import DelegationAuthority
import time


class AllowPolicy:
    def authorize(self, request):
        return AuthorizationDecision(True, "decision-1", "policy-v1", "allowed")


class SpecialistCoordinatorGatewayTests(unittest.TestCase):
    def test_coordinator_uses_gateway_and_audits_plan_authorization(self):
        with tempfile.TemporaryDirectory() as directory:
            database = str(Path(directory) / "yuki.sqlite3")
            store = WorkflowRunStore(database)
            authority = DelegationAuthority(database)
            runtime = TaskRuntime(
                store,
                authorize=lambda actor, action, task: actor == "alice" and action in {"task:create", "task:read"},
                delegation_authority=authority,
            )
            events = []
            gateway = AuthorizationGateway(
                AllowPolicy(),
                audit=lambda request, decision: events.append(
                    (request.action, request.resource, decision.allowed)
                ),
            )
            coordinator = SpecialistAgentCoordinator(
                runtime, database,
                [SpecialistAgent("research", "Research", frozenset({"research"}), "emit")],
                authorization_gateway=gateway,
                principal_for_actor=lambda actor: Principal(actor, "human", tenant_id="tenant-a"),
                workload_principal_for_agent=lambda agent_id: Principal(
                    "workload:" + agent_id, "workload", tenant_id="tenant-a",
                    agent_id=agent_id,
                    permissions=frozenset({"task:progress", "task:complete"}),
                ),
                policy_revision="policy-v1",
            )
            plan = coordinator.submit(
                actor_id="alice", idempotency_key="gateway-plan",
                subtasks=[SubtaskSpec("collect", "Collect sources", ("research",))],
            )
            self.assertEqual(len(plan.subtasks), 1)
            self.assertEqual(events[0], ("agent:plan", "agent-resource:collection", True))
            dispatched = coordinator.advance(actor_id="alice", plan_id=plan.plan_id)
            subtask = dispatched.subtasks[0]
            claimed = store.claim_next(worker_id="workload:research")
            self.assertEqual(claimed.run_id, subtask.task_id)
            grant = coordinator.issue_workload_delegation(
                actor_id="alice", plan_id=plan.plan_id, subtask_id="collect",
                scopes={"task:progress", "task:complete"},
                expires_at=int(time.time()) + 300,
            )
            verified = authority.verify(
                grant.delegation_id, grantee_id="workload:research", tenant_id="tenant-a",
                agent_id="research", task_id=subtask.task_id,
                lease_generation=claimed.lease_generation, scopes={"task:progress"}, 
            )
            self.assertEqual(verified.grantor_id, "alice")

    def test_delegation_fails_closed_without_trusted_workload_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            database = str(Path(directory) / "yuki.sqlite3")
            store = WorkflowRunStore(database)
            authority = DelegationAuthority(str(Path(directory) / "delegations.sqlite3"))
            runtime = TaskRuntime(store, authorize=lambda actor, action, task: True,
                                  delegation_authority=authority)
            gateway = AuthorizationGateway(AllowPolicy(), audit=lambda request, decision: None)
            coordinator = SpecialistAgentCoordinator(
                runtime, database,
                [SpecialistAgent("research", "Research", frozenset({"research"}), "emit")],
                authorization_gateway=gateway,
                principal_for_actor=lambda actor: Principal(actor, "human", tenant_id="tenant-a"),
            )
            plan = coordinator.submit(
                actor_id="alice", idempotency_key="missing-workload-identity",
                subtasks=[SubtaskSpec("collect", "Collect sources", ("research",), requires_approval=False)],
            )
            dispatched = coordinator.advance(actor_id="alice", plan_id=plan.plan_id)
            with self.assertRaises(PermissionError):
                coordinator.issue_workload_delegation(
                    actor_id="alice", plan_id=plan.plan_id, subtask_id="collect",
                    scopes={"task:progress"}, expires_at=int(time.time()) + 300,
                )


if __name__ == "__main__":
    unittest.main()
