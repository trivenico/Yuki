"""Tests for authenticated durable task ownership and read isolation."""
from pathlib import Path
import tempfile
import unittest

from core.auth import AuthenticationError, BuiltInAuthService
from core.durable_task_engine import DurableTaskEngine, TaskNotFound
from core.authenticated_task_service import AuthenticatedDurableTaskService


class AuthenticatedTaskServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.auth = BuiltInAuthService(root / "auth.sqlite", password_iterations=100_000)
        self.engine = DurableTaskEngine(root / "tasks.sqlite")
        self.addCleanup(self.engine.close)
        self.service = AuthenticatedDurableTaskService(self.auth, self.engine)
        self.alice = self.auth.create_account("alice.user", "a-strong-password-001")
        self.bob = self.auth.create_account("bob.user", "a-strong-password-002")
        self.alice_session = self.auth.authenticate("alice.user", "a-strong-password-001")
        self.bob_session = self.auth.authenticate("bob.user", "a-strong-password-002")

    def test_create_goal_derives_owner_from_verified_session(self):
        goal = self.service.create_goal(self.alice_session.token)
        stored = self.engine.get_goal(goal)
        self.assertEqual(stored["owner_id"], self.alice.user_id)
        self.assertNotEqual(stored["owner_id"], self.bob.user_id)

    def test_submit_derives_owner_and_does_not_trust_payload_claim(self):
        goal = self.service.create_goal(self.alice_session.token)
        task = self.service.submit(
            self.alice_session.token, goal, "skill.invoke",
            {"principal_id": self.bob.user_id, "skill_name": "read", "argument": ""},
            idempotency_key="task-1", max_attempts=1,
        )
        self.assertEqual(task["owner_id"], self.alice.user_id)
        self.assertEqual(task["payload"]["principal_id"], self.bob.user_id)

    def test_other_user_cannot_read_or_submit_to_goal(self):
        goal = self.service.create_goal(self.alice_session.token)
        with self.assertRaises(TaskNotFound):
            self.service.get_goal(self.bob_session.token, goal)
        with self.assertRaises(TaskNotFound):
            self.service.submit(
                self.bob_session.token, goal, "work", {},
                idempotency_key="bob-task",
            )

    def test_other_user_cannot_read_tasks_or_events_or_cancel(self):
        goal = self.service.create_goal(self.alice_session.token)
        task = self.service.submit(
            self.alice_session.token, goal, "work", {},
            idempotency_key="alice-task",
        )
        for action in (
            lambda: self.service.get_task(self.bob_session.token, task["id"]),
            lambda: self.service.list_tasks(self.bob_session.token, goal),
            lambda: self.service.list_events(self.bob_session.token, goal),
            lambda: self.service.cancel_task(self.bob_session.token, task["id"]),
        ):
            with self.subTest(action=action), self.assertRaises(TaskNotFound):
                action()

    def test_revoked_or_invalid_session_is_rejected(self):
        goal = self.service.create_goal(self.alice_session.token)
        self.assertTrue(self.auth.revoke_session(self.alice_session.token))
        with self.assertRaises(AuthenticationError):
            self.service.create_goal(self.alice_session.token)
        with self.assertRaises(AuthenticationError):
            self.service.get_goal("invalid-session-token", goal)

    def test_ownerless_legacy_goal_is_not_visible_through_authenticated_facade(self):
        legacy_goal = self.engine.create_goal()
        with self.assertRaises(TaskNotFound):
            self.service.get_goal(self.alice_session.token, legacy_goal)
        with self.assertRaises(TaskNotFound):
            self.service.submit(
                self.alice_session.token, legacy_goal, "work", {},
                idempotency_key="legacy-claim",
            )


if __name__ == "__main__":
    unittest.main()
