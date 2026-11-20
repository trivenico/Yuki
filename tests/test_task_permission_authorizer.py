"""Tests for the desktop task/agent permission bridge."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from core.auth import BuiltInAuthService, task_permission_authorizer


class TaskPermissionAuthorizerTests(unittest.TestCase):
    def test_admin_authorizes_only_explicitly_mapped_live_session_actions(self):
        with tempfile.TemporaryDirectory() as directory:
            auth = BuiltInAuthService(
                str(Path(directory) / "auth.sqlite3"), password_iterations=100_000,
            )
            owner = auth.bootstrap_admin("owner", "a-long-test-password-123")
            issued = auth.authenticate("owner", "a-long-test-password-123")
            token = issued.token
            authorize = task_permission_authorizer(auth, lambda: token)

            self.assertTrue(authorize(owner.user_id, "task:create", ""))
            self.assertTrue(authorize(owner.user_id, "task:session:revoke", ""))
            self.assertTrue(authorize(owner.user_id, "agent:dispatch", "plan-1"))
            self.assertTrue(authorize(owner.user_id, "task:list", ""))
            self.assertFalse(authorize("forged-owner", "task:create", ""))
            self.assertFalse(authorize(owner.user_id, "task:complete", "task-1"))
            self.assertFalse(authorize(owner.user_id, "unknown:admin", ""))

            auth.revoke_session(token)
            self.assertFalse(authorize(owner.user_id, "task:create", ""))

    def test_basic_user_is_read_only_under_workflow_permission_mapping(self):
        with tempfile.TemporaryDirectory() as directory:
            auth = BuiltInAuthService(
                str(Path(directory) / "auth.sqlite3"), password_iterations=100_000,
            )
            user = auth.create_account("reader", "another-long-test-password-456")
            issued = auth.authenticate("reader", "another-long-test-password-456")
            authorize = task_permission_authorizer(auth, lambda: issued.token)

            self.assertTrue(authorize(user.user_id, "task:list", ""))
            self.assertTrue(authorize(user.user_id, "agent:read", ""))
            self.assertFalse(authorize(user.user_id, "task:approve", "task-1"))
            self.assertFalse(authorize(user.user_id, "agent:dispatch", "plan-1"))


if __name__ == "__main__":
    unittest.main()
