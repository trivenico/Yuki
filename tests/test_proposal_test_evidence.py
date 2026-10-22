import tempfile
import unittest
from pathlib import Path

from core.proposal_test_evidence import ProposalTestEvidenceError, ProposalTestEvidenceStore


class ProposalTestEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = ProposalTestEvidenceStore(Path(self.temp.name) / "evidence.sqlite3")

    def _result(self, code=0):
        return {
            "command": ["python", "-m", "unittest", "discover"],
            "returncode": code, "timed_out": False, "output_truncated": False,
            "cleanup_attempted": True, "stdout_sha256": "a" * 64, "stderr_sha256": "b" * 64,
        }

    def test_append_and_list_evidence_without_raw_output(self):
        run_id = self.store.append(proposal_sha256="c" * 64, workspace_name="yuki-improvement-demo", results=[self._result()])
        records = self.store.list_recent()
        self.assertEqual(records[0]["run_id"], run_id)
        self.assertEqual(records[0]["proposal_sha256"], "c" * 64)
        self.assertNotIn("stdout", records[0]["results"][0])
        self.assertEqual(records[0]["previous_hash"], "0" * 64)

    def test_evidence_is_append_only(self):
        self.store.append(proposal_sha256="c" * 64, workspace_name="yuki-improvement-demo", results=[self._result()])
        with self.assertRaises(Exception):
            with self.store._connect() as db:
                db.execute("DELETE FROM proposal_test_evidence")

    def test_rejects_invalid_digest_and_workspace(self):
        with self.assertRaises(ProposalTestEvidenceError):
            self.store.append(proposal_sha256="bad", workspace_name="yuki-improvement-demo", results=[self._result()])
        with self.assertRaises(ProposalTestEvidenceError):
            self.store.append(proposal_sha256="c" * 64, workspace_name="../outside", results=[self._result()])


if __name__ == "__main__":
    unittest.main()
