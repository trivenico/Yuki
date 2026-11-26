"""Unit tests for the typed PostgreSQL agent-plan adapter boundary."""
import unittest
from datetime import datetime, timezone

from automation.postgres_agent_plan_adapter import PostgresAgentPlanRepositoryAdapter


class PostgresAgentPlanAdapterTests(unittest.TestCase):
    def test_row_bundle_converts_to_typed_record_and_uses_persisted_id(self):
        bundle = {
            "plan": {
                "plan_id": "ap_canonical", "tenant_id": "tenant-a",
                "owner_principal_id": "owner-a", "client_idempotency_key": "request-1",
                "request_digest": "a" * 64,
                "created_at": datetime(2026, 11, 25, tzinfo=timezone.utc),
            },
            "subtasks": [{
                "subtask_id": "research", "agent_id": "researcher",
                "action": "summarize", "description": "Research safely",
                "capabilities_json": ["research"],
                "permissions_json": ["web:read"],
                "dependencies_json": [], "requires_approval": True,
                "task_id": None, "status": "pending",
            }],
        }
        record = PostgresAgentPlanRepositoryAdapter._record(bundle)
        self.assertEqual("ap_canonical", record.plan_id)
        self.assertEqual("request-1", record.client_idempotency_key)
        self.assertTrue(record.idempotency_key.startswith("tenant:"))
        self.assertEqual(("research",), tuple(item.subtask_id for item in record.subtasks))
        self.assertEqual(("web:read",), record.subtasks[0].required_permissions)
        self.assertEqual("2026-11-25T00:00:00+00:00", record.created_at)

    def test_malformed_persisted_security_fields_fail_closed(self):
        bundle = {
            "plan": {
                "plan_id": "ap_canonical", "tenant_id": "tenant-a",
                "owner_principal_id": "owner-a", "client_idempotency_key": "request-1",
                "request_digest": "a" * 64, "created_at": "2026-11-25T00:00:00+00:00",
            },
            "subtasks": [{
                "subtask_id": "research", "agent_id": "researcher",
                "action": "summarize", "description": "Research safely",
                "capabilities_json": "not-a-list", "permissions_json": [],
                "dependencies_json": [], "requires_approval": True,
                "task_id": None, "status": "pending",
            }],
        }
        with self.assertRaisesRegex(RuntimeError, "persisted_subtask_invalid"):
            PostgresAgentPlanRepositoryAdapter._record(bundle)

    def test_distributed_scope_rejects_legacy_unowned_records(self):
        with self.assertRaisesRegex(ValueError, "owner_scope_required"):
            PostgresAgentPlanRepositoryAdapter._scope(None, None)

    def test_reservation_conversion_preserves_expiry_and_generation(self):
        class Reservation:
            plan_id = "ap_plan"
            subtask_id = "research"
            tenant_id = "tenant-a"
            owner_principal_id = "owner-a"
            generation = 7
            claim_token = "reservation-token"
            state = "reserved"
            task_id = None
            expires_at = "2026-11-25T12:00:00+00:00"

        value = PostgresAgentPlanRepositoryAdapter._reservation(Reservation())
        self.assertEqual(7, value.generation)
        self.assertEqual("reservation-token", value.claim_token)
        self.assertEqual("2026-11-25T12:00:00+00:00", value.expires_at)


if __name__ == "__main__":
    unittest.main()
