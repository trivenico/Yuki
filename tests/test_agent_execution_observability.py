"""Tests for privacy-safe agent execution observability."""
import tempfile
import unittest
from pathlib import Path

from automation.agent_execution_observability import AgentExecutionTraceStore, ExecutionTraceError


class AgentExecutionTraceStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.now = [1000.0]
        self.store = AgentExecutionTraceStore(
            str(Path(self.tmp.name) / "traces.sqlite3"), clock=lambda: self.now[0],
        )

    def test_parent_child_tree_and_root_resource_totals(self):
        root = self.store.start_execution(agent_id="orchestrator", task_id="task-1")
        child = self.store.start_execution(
            agent_id="research-agent", parent_execution_id=root["execution_id"],
            task_id="task-2", provider_execution_id="provider-exec-1",
        )
        self.store.record_event(
            execution_id=child["execution_id"], event_type="provider.completed",
            actor_id="research-agent", input_tokens=120, output_tokens=45,
            duration_ms=800, cost_microunits=17, status="succeeded",
        )
        self.assertEqual(child["root_execution_id"], root["execution_id"])
        self.assertEqual(len(self.store.list_children(root["execution_id"])), 1)
        totals = self.store.resource_totals(root["execution_id"])
        self.assertEqual(totals, {
            "input_tokens": 120, "output_tokens": 45,
            "duration_ms": 800, "cost_microunits": 17,
        })

    def test_task_binding_rejects_conflicts(self):
        root = self.store.start_execution(agent_id="coordinator")
        child = self.store.start_execution(agent_id="research", parent_execution_id=root["execution_id"])
        self.store.bind_task(child["execution_id"], task_id="task-123")
        self.assertEqual(self.store.get_execution_by_task("task-123", agent_id="research")["execution_id"], child["execution_id"])
        with self.assertRaises(ExecutionTraceError):
            self.store.bind_task(child["execution_id"], task_id="task-456")
        with self.assertRaises(ExecutionTraceError):
            self.store.bind_task(root["execution_id"], task_id="task-123")


    def test_transactional_outbox_claim_ack_and_retry(self):
        root = self.store.start_execution(agent_id="coordinator")
        event_id = self.store.record_event(
            execution_id=root["execution_id"], event_type="execution.started",
            actor_id="coordinator", status="running",
        )
        claimed = self.store.claim_outbox(worker_id="dispatcher", limit=10)
        self.assertIn(event_id, {item["event_id"] for item in claimed})
        item = next(item for item in claimed if item["event_id"] == event_id)
        self.assertFalse(self.store.acknowledge_outbox(worker_id="other", event_id=event_id))
        self.assertTrue(self.store.retry_outbox(
            worker_id="dispatcher", event_id=event_id, error_code="sink_unavailable", delay_seconds=5,
        ))
        self.assertFalse(any(x["event_id"] == event_id for x in self.store.claim_outbox(worker_id="dispatcher")))
        self.now[0] += 5
        retried = self.store.claim_outbox(worker_id="dispatcher")
        self.assertIn(event_id, {item["event_id"] for item in retried})
        self.assertTrue(self.store.acknowledge_outbox(worker_id="dispatcher", event_id=event_id))
        self.assertFalse(self.store.acknowledge_outbox(worker_id="dispatcher", event_id=event_id))

    def test_every_created_execution_event_enters_outbox_atomically(self):
        root = self.store.start_execution(agent_id="coordinator")
        claimed = self.store.claim_outbox(worker_id="dispatcher")
        created = [item for item in claimed if item["execution_id"] == root["execution_id"]]
        self.assertEqual([item["event_type"] for item in created], ["execution.created"])

    def test_permission_decisions_are_structured_and_auditable(self):
        root = self.store.start_execution(agent_id="orchestrator")
        self.store.record_event(
            execution_id=root["execution_id"], event_type="permission.denied",
            actor_id="orchestrator", permission="tool:browser", decision="denied",
        )
        event = self.store.list_events(root["execution_id"])[-1]
        self.assertEqual(event["decision"], "denied")
        self.assertEqual(event["permission"], "tool:browser")

    def test_execution_timeline_joins_tree_events_and_resource_totals(self):
        root = self.store.start_execution(agent_id='coordinator', task_id='plan-1')
        specialist = self.store.start_execution(agent_id='research', parent_execution_id=root['execution_id'], task_id='task-1')
        provider = self.store.start_execution(agent_id='provider.local', parent_execution_id=specialist['execution_id'], provider_execution_id='provider-exec-1')
        self.store.record_event(execution_id=specialist['execution_id'], event_type='agent.accepted', actor_id='coordinator', status='running')
        self.store.record_event(execution_id=provider['execution_id'], event_type='provider.completed', actor_id='research', input_tokens=10, output_tokens=4, duration_ms=25, cost_microunits=7, status='succeeded')
        timeline = self.store.execution_timeline(root['execution_id'])
        self.assertEqual(len(timeline['executions']), 3)
        self.assertEqual(timeline['executions'][2]['provider_execution_id'], 'provider-exec-1')
        self.assertEqual([event['event_type'] for event in timeline['events']], ['execution.created', 'execution.created', 'execution.created', 'agent.accepted', 'provider.completed'])
        self.assertEqual(timeline['resource_totals'], {'input_tokens': 10, 'output_tokens': 4, 'duration_ms': 25, 'cost_microunits': 7})
        self.assertTrue(timeline['complete'])

    def test_timeline_rejects_unknown_root_and_bounds(self):
        with self.assertRaises(KeyError):
            self.store.execution_timeline('missing-root')
        root = self.store.start_execution(agent_id='coordinator')
        with self.assertRaises(ValueError):
            self.store.execution_timeline(root['execution_id'], max_events=0)
        with self.assertRaises(ExecutionTraceError):
            self.store.execution_timeline(root['execution_id'], max_executions=1) if self.store.start_execution(agent_id='child', parent_execution_id=root['execution_id']) else None

    def test_sensitive_payload_fields_are_not_part_of_event_schema(self):
        root = self.store.start_execution(agent_id="orchestrator")
        event = self.store.list_events(root["execution_id"])[0]
        self.assertNotIn("prompt", event)
        self.assertNotIn("payload", event)
        self.assertNotIn("credential", event)

    def test_unknown_event_and_invalid_metrics_fail_closed(self):
        root = self.store.start_execution(agent_id="orchestrator")
        with self.assertRaises(ExecutionTraceError):
            self.store.record_event(
                execution_id=root["execution_id"], event_type="arbitrary.payload",
                actor_id="orchestrator",
            )
        with self.assertRaises(ExecutionTraceError):
            self.store.record_event(
                execution_id=root["execution_id"], event_type="execution.completed",
                actor_id="orchestrator", input_tokens=-1,
            )

    def test_missing_parent_and_unbounded_queries_are_rejected(self):
        with self.assertRaises(KeyError):
            self.store.start_execution(agent_id="worker", parent_execution_id="missing-parent")
        root = self.store.start_execution(agent_id="orchestrator")
        with self.assertRaises(ValueError):
            self.store.list_events(root["execution_id"], limit=0)


if __name__ == "__main__":
    unittest.main()
