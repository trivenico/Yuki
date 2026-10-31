import unittest
from copy import deepcopy

from core.sync_contract import SyncContractError, validate_sync_event


def valid_event():
    return {
        "event_id": "event_00000001",
        "device_id": "device_0000001",
        "collection": "task_records",
        "object_id": "task_00000001",
        "revision": 1,
        "occurred_at": "2026-10-31T10:00:00Z",
        "operation": "upsert",
        "payload": {"title": "Prepare release", "status": "open"},
    }


class SyncContractTests(unittest.TestCase):
    def test_accepts_allowlisted_event_and_normalizes_timestamp(self):
        event = validate_sync_event(valid_event())
        self.assertEqual(event.collection, "task_records")
        self.assertTrue(event.occurred_at.endswith("+00:00"))

    def test_rejects_unapproved_collection(self):
        raw = valid_event()
        raw["collection"] = "workspace_files"
        with self.assertRaises(SyncContractError):
            validate_sync_event(raw)

    def test_rejects_secret_like_fields_recursively(self):
        raw = valid_event()
        raw["payload"]["nested"] = {"openai_api_key": "must-not-sync"}
        with self.assertRaises(SyncContractError):
            validate_sync_event(raw)

    def test_rejects_naive_timestamp(self):
        raw = valid_event()
        raw["occurred_at"] = "2026-10-31T10:00:00"
        with self.assertRaises(SyncContractError):
            validate_sync_event(raw)

    def test_rejects_unknown_envelope_fields(self):
        raw = valid_event()
        raw["arbitrary"] = "value"
        with self.assertRaises(SyncContractError):
            validate_sync_event(raw)

    def test_delete_must_have_empty_payload(self):
        raw = valid_event()
        raw["operation"] = "delete"
        with self.assertRaises(SyncContractError):
            validate_sync_event(raw)

    def test_delete_accepts_empty_payload(self):
        raw = valid_event()
        raw["operation"] = "delete"
        raw["payload"] = {}
        self.assertEqual(validate_sync_event(raw).operation, "delete")


if __name__ == "__main__":
    unittest.main()
