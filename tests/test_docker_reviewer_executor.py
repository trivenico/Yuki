import unittest
from unittest.mock import patch

from automation.docker_reviewer_executor import (
    DockerReviewerExecutionError, DockerReviewerExecutor, ReviewerContainerCommand,
)
from automation.reviewer_contracts import ReviewTask


class DockerReviewerExecutorContractTests(unittest.TestCase):
    def setUp(self):
        self.task = ReviewTask(
            task_id="review-1", artifact_sha256="a" * 64,
            author_agent_id="author", reviewer_agent_id="reviewer",
            check_name="tests", requirements=("Inspect test evidence.",),
            evidence_references=("evidence://tests",),
        )

    def executor(self):
        with patch("automation.docker_reviewer_executor.shutil.which", return_value="/usr/bin/docker"):
            return DockerReviewerExecutor(
                image="registry.example/reviewer@sha256:" + "b" * 64,
                command=ReviewerContainerCommand(argv=("/opt/yuki/reviewer",)),
            )

    def test_request_contains_only_task_and_assigned_evidence(self):
        executor = self.executor()
        raw = executor._request(self.task, {"evidence://tests": b"test output"})
        self.assertNotIn(b"session_token", raw)
        self.assertNotIn(b"signing_key", raw)
        self.assertIn(b"evidence_base64", raw)

    def test_evidence_scope_mismatch_is_rejected(self):
        with self.assertRaisesRegex(DockerReviewerExecutionError, "scope_mismatch"):
            self.executor()._request(self.task, {"evidence://other": b"secret"})

    def test_result_is_bound_to_task_and_validated(self):
        raw = b'{"status":"passed","summary":"Reviewed tests.","findings":[]}'
        result = self.executor()._parse_result(raw, self.task)
        self.assertEqual(result.task_id, self.task.task_id)
        self.assertEqual(result.status, "passed")

    def test_malformed_or_unbound_result_is_rejected(self):
        with self.assertRaises(DockerReviewerExecutionError):
            self.executor()._parse_result(b'{"status":"passed"}', self.task)

    def test_image_must_be_digest_pinned(self):
        with patch("automation.docker_reviewer_executor.shutil.which", return_value="/usr/bin/docker"):
            with self.assertRaisesRegex(ValueError, "digest_pinned"):
                DockerReviewerExecutionError  # keep exception type import exercised
                DockerReviewerExecutor(
                    image="registry.example/reviewer:latest",
                    command=ReviewerContainerCommand(argv=("/review",)),
                )


if __name__ == "__main__":
    unittest.main()
