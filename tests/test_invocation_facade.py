"""Tests for the explicit-review invocation facade."""
import unittest
from unittest.mock import Mock

from core.invocation_facade import InvocationFacade


class InvocationFacadeTests(unittest.TestCase):
    def setUp(self):
        self.dispatcher = Mock()
        self.dispatcher.request_approval.return_value = {
            "approval_id": "secret-bearer-token",
            "skill_id": "notes.read",
            "risk": "medium",
            "arguments": {"path": "readme.txt"},
            "payload_sha256": "a" * 64,
            "expires_at": "2099-01-01T00:00:00Z",
        }
        self.dispatcher.execute.return_value = {"ok": True, "output": {"text": "hello"}}
        self.facade = InvocationFacade(self.dispatcher)

    def test_confirmation_sees_review_metadata_but_not_token(self):
        seen = []
        result = self.facade.invoke("notes.read", {"path": "readme.txt"},
                                    confirm=lambda review: seen.append(review) or True)
        self.assertTrue(result["ok"])
        self.assertNotIn("approval_id", seen[0])
        self.assertEqual(seen[0]["payload_sha256"], "a" * 64)
        self.dispatcher.execute.assert_called_once_with(
            "notes.read", {"path": "readme.txt"}, approval_id="secret-bearer-token"
        )

    def test_denial_revokes_token_and_never_executes(self):
        result = self.facade.invoke("notes.read", {"path": "readme.txt"}, confirm=lambda _: False)
        self.assertEqual(result["status"], "denied")
        self.dispatcher.approval_store.deny.assert_called_once_with("secret-bearer-token")
        self.dispatcher.execute.assert_not_called()

    def test_confirmation_exception_revokes_token(self):
        def fail(_):
            raise RuntimeError("UI unavailable")
        result = self.facade.invoke("notes.read", {"path": "readme.txt"}, confirm=fail)
        self.assertEqual(result["status"], "confirmation_failed")
        self.dispatcher.approval_store.deny.assert_called_once_with("secret-bearer-token")
        self.dispatcher.execute.assert_not_called()

    def test_requires_explicit_confirmation_callback(self):
        with self.assertRaises(TypeError):
            self.facade.invoke("notes.read", {"path": "readme.txt"}, confirm=None)

    def test_execution_failure_returns_safe_status(self):
        self.dispatcher.execute.side_effect = RuntimeError("secret internal detail")
        result = self.facade.invoke("notes.read", {"path": "readme.txt"}, confirm=lambda _: True)
        self.assertEqual(result, {"ok": False, "status": "execution_failed"})
        self.assertNotIn("secret internal detail", str(result))


if __name__ == "__main__":
    unittest.main()
