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

    def test_permission_decisions_are_structured_and_auditable(self):
        root = self.store.start_execution(agent_id="orchestrator")
        self.store.record_event(
            execution_id=root["execution_id"], event_type="permission.denied",
            actor_id="orchestrator", permission="tool:browser", decision="denied",
        )
        event = self.store.list_events(root["execution_id"])[-1]
        self.assertEqual(event["decision"], "denied")
        self.assertEqual(event["permission"], "tool:browser")

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
