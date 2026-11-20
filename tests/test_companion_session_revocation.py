"""Regression coverage for owner-session task revocation delegation."""
from __future__ import annotations

import unittest
from unittest.mock import Mock

from companion.application import CompanionApplicationService


class CompanionSessionRevocationTests(unittest.TestCase):
    def test_revocation_passes_actor_and_exact_session_to_task_runtime(self):
        runtime = Mock()
        runtime.revoke_session_approvals.return_value = ["cancelled-task"]
        service = object.__new__(CompanionApplicationService)
        service.task_runtime = runtime

        result = service.revoke_session_tasks(
            actor_id="owner-123", session_id="live-session-secret",
        )

        self.assertEqual(result, ["cancelled-task"])
        runtime.revoke_session_approvals.assert_called_once_with(
            actor_id="owner-123", session_id="live-session-secret",
        )

    def test_revocation_without_task_runtime_is_a_noop(self):
        service = object.__new__(CompanionApplicationService)
        service.task_runtime = None

        self.assertEqual(
            service.revoke_session_tasks(
                actor_id="owner-123", session_id="live-session-secret",
            ),
            [],
        )


if __name__ == "__main__":
    unittest.main()
