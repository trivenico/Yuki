import json
from contextlib import closing
import sqlite3
import tempfile
import unittest
from pathlib import Path

from core.engineering_evidence_store import (
    EngineeringEvidenceError,
    SQLiteEngineeringEvidenceStore,
)

SHA = "a" * 40


class EngineeringEvidenceStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.db_path = str(Path(self.temp_dir.name) / "evidence.sqlite3")
        self.store = SQLiteEngineeringEvidenceStore(self.db_path)

    def test_appends_and_reads_canonical_snapshot(self):
        payload = {"decision": "block", "reasons": ["missing_check"], "checks": 3}
        evidence_id = self.store.append(
            repository="Trivenico/Yuki", pr_number=73, head_sha=SHA, payload=payload
        )
        rows = self.store.list_for_pull_request(repository="trivenico/Yuki", pr_number=73)
        self.assertEqual(evidence_id, rows[0]["evidence_id"])
        self.assertEqual(rows[0]["payload"], payload)
        self.assertEqual(rows[0]["repository"], "trivenico/yuki")
        self.assertTrue(self.store.verify_chain())

    def test_chain_detects_payload_tampering(self):
        self.store.append(repository="trivenico/Yuki", pr_number=73, head_sha=SHA,
                          payload={"decision": "block"})
        with closing(sqlite3.connect(self.db_path)) as db:
            db.execute("DROP TRIGGER engineering_evidence_no_update")
            db.execute("UPDATE engineering_evidence SET payload_json = ?", ('{"decision":"allow"}',))
            db.commit()
        self.assertFalse(self.store.verify_chain())

    def test_normal_api_rejects_update_and_delete(self):
        self.store.append(repository="trivenico/Yuki", pr_number=73, head_sha=SHA,
                          payload={"decision": "block"})
        with closing(sqlite3.connect(self.db_path)) as db:
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("UPDATE engineering_evidence SET payload_json = ?", ('{"decision":"allow"}',))
            with self.assertRaises(sqlite3.IntegrityError):
                db.execute("DELETE FROM engineering_evidence")

    def test_rejects_invalid_identity_and_non_json_values(self):
        with self.assertRaises(EngineeringEvidenceError):
            self.store.append(repository="../Yuki", pr_number=73, head_sha=SHA, payload={})
        with self.assertRaises(EngineeringEvidenceError):
            self.store.append(repository="trivenico/Yuki", pr_number=True, head_sha=SHA, payload={})
        with self.assertRaises(EngineeringEvidenceError):
            self.store.append(repository="trivenico/Yuki", pr_number=73, head_sha="bad", payload={})
        with self.assertRaises(EngineeringEvidenceError):
            self.store.append(repository="trivenico/Yuki", pr_number=73, head_sha=SHA,
                              payload={"not_json": object()})

    def test_rejects_oversized_payload_and_invalid_limits(self):
        with self.assertRaises(EngineeringEvidenceError):
            self.store.append(repository="trivenico/Yuki", pr_number=73, head_sha=SHA,
                              payload={"large": "x" * 1_000_001})
        with self.assertRaises(ValueError):
            self.store.list_for_pull_request(repository="trivenico/Yuki", pr_number=73, limit=0)

    def test_empty_chain_verifies(self):
        self.assertTrue(self.store.verify_chain())


if __name__ == "__main__":
    unittest.main()
