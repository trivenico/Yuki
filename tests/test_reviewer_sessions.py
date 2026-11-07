import tempfile
import sqlite3
import unittest
from pathlib import Path

from automation.reviewer_sessions import ReviewerSessionAuthority


class ReviewerSessionAuthorityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.digest = "d" * 64
        self.allowed = True
        self.authority = ReviewerSessionAuthority(
            Path(self.tmp.name) / "sessions.sqlite",
            secret=b"s" * 32,
            authorize=lambda reviewer, action, digest: (
                self.allowed and reviewer == "reviewer-1" and digest == self.digest
                and action in {"review:execute", "review:submit"}
            ),
        )
        self.token = self.authority.issue(
            reviewer_agent_id="reviewer-1", artifact_sha256=self.digest
        )

    def tearDown(self):
        self.tmp.cleanup()

    def test_session_is_bound_to_reviewer_artifact_and_action(self):
        self.assertTrue(self.authority.validate(
            self.token, reviewer_agent_id="reviewer-1",
            artifact_sha256=self.digest, action="review:execute", operation_id="task-1",
        ))
        self.assertFalse(self.authority.validate(
            self.token, reviewer_agent_id="reviewer-1",
            artifact_sha256="e" * 64, action="review:submit", operation_id="task-2",
        ))

    def test_operation_replay_is_rejected(self):
        args = dict(reviewer_agent_id="reviewer-1", artifact_sha256=self.digest,
                    action="review:submit", operation_id="task-1")
        self.assertTrue(self.authority.validate(self.token, **args))
        self.assertFalse(self.authority.validate(self.token, **args))

    def test_tampered_token_is_rejected(self):
        token = self.token[:-1] + ("0" if self.token[-1] != "0" else "1")
        self.assertFalse(self.authority.validate(
            token, reviewer_agent_id="reviewer-1", artifact_sha256=self.digest,
            action="review:execute", operation_id="task-1",
        ))

    def test_revoked_session_is_rejected(self):
        payload = self.authority._decode(self.token)
        self.authority.revoke(session_id=payload["session_id"])
        self.assertFalse(self.authority.validate(
            self.token, reviewer_agent_id="reviewer-1",
            artifact_sha256=self.digest, action="review:execute", operation_id="task-1",
        ))

    def test_persisted_session_claim_mismatch_is_rejected(self):
        payload = self.authority._decode(self.token)
        with sqlite3.connect(self.authority.database_path) as db:
            db.execute(
                "UPDATE reviewer_sessions SET artifact_sha256=? WHERE session_id=?",
                ("e" * 64, payload["session_id"]),
            )
        self.assertFalse(self.authority.validate(
            self.token, reviewer_agent_id="reviewer-1",
            artifact_sha256=self.digest, action="review:execute", operation_id="task-db-tamper",
        ))

    def test_invalid_action_collection_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "invalid_reviewer_session_actions"):
            self.authority.issue(
                reviewer_agent_id="reviewer-1", artifact_sha256=self.digest,
                actions="review:execute",
            )

    def test_authorization_is_rechecked_at_use(self):
        self.allowed = False
        self.assertFalse(self.authority.validate(
            self.token, reviewer_agent_id="reviewer-1",
            artifact_sha256=self.digest, action="review:execute", operation_id="task-1",
        ))


if __name__ == "__main__":
    unittest.main()
