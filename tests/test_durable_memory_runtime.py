import unittest
from unittest.mock import Mock

from core.durable_memory_runtime import DurableMemoryRuntime


class DurableMemoryRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.memory = Mock()
        self.memory.add.return_value = {"id": "mem-1"}
        self.memory.delete.return_value = True
        self.memory.clear.return_value = 2
        self.coordinator = Mock()
        self.coordinator.record.return_value.pending_id = "pending-1"
        self.coordinator.approve.return_value.status = "completed"
        self.coordinator.approve.return_value.result = {
            "ok": True, "message": "Memory saved.", "data": {"id": "mem-1"}
        }
        self.runtime = DurableMemoryRuntime(
            memory=self.memory, coordinator=self.coordinator, actor="local-owner",
            confirm=lambda action: True, session_id="test-session",
        )

    def test_add_goes_through_durable_coordinator_before_mutation(self):
        result = self.runtime.mutate("memory:add", {"text": "hello"})
        self.assertTrue(result.ok)
        self.coordinator.record.assert_called_once()
        self.coordinator.approve.assert_called_once()
        self.memory.add.assert_not_called()
        self.assertEqual(self.coordinator.approve.call_args.kwargs["actor"], "local-owner")

    def test_approved_executor_only_accepts_allowlisted_operation(self):
        result = self.runtime.execute_approved_action({
            "skill_id": "memory:add", "arguments": {"text": "hello"}
        })
        self.assertTrue(result["ok"])
        self.memory.add.assert_called_once_with("hello")

    def test_rejects_unlisted_operation(self):
        result = self.runtime.mutate("memory:export", {})
        self.assertFalse(result.ok)
        self.coordinator.record.assert_not_called()

    def test_failed_approval_does_not_execute_memory_mutation(self):
        self.coordinator.approve.return_value.status = "approval_denied"
        result = self.runtime.mutate("memory:clear", {})
        self.assertFalse(result.ok)
        self.memory.clear.assert_not_called()

    def test_clear_rejects_unexpected_payload(self):
        with self.assertRaises(ValueError):
            self.runtime.execute_approved_action({
                "skill_id": "memory:clear", "arguments": {"all": True}
            })


if __name__ == "__main__":
    unittest.main()
