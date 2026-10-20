"""Phase 46 tests for the runtime's controlled manifest invocation path."""
from __future__ import annotations

import os
import unittest
from unittest.mock import Mock, patch

from core.agent import YukiCoreAgent
from skills import TimeSkill


class Phase46InvocationIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.dispatcher = Mock()
        self.dispatcher.request_approval.return_value = {
            "approval_id": "do-not-display-this-token",
            "skill_id": "notes.read",
            "risk": "low",
            "arguments": {"path": "readme.txt"},
            "payload_sha256": "b" * 64,
            "expires_at": "2099-01-01T00:00:00Z",
        }
        self.dispatcher.execute.return_value = {
            "ok": True, "skill_id": "notes.read", "output": {"text": "hello"}
        }
        self.agent = YukiCoreAgent(skills=[TimeSkill()], dispatcher=self.dispatcher)

    def test_manifest_invocation_uses_explicit_review_and_dispatcher(self):
        seen = []
        with patch.dict(os.environ, {"YUKI_ENV": "development"}):
            response = self.agent.invoke_manifest_skill(
                "notes.read", {"path": "readme.txt"},
                confirm=lambda review: seen.append(review) or True,
            )
        self.assertTrue(response.ok)
        self.assertEqual(response.stage, "report")
        self.assertEqual(seen[0]["payload_sha256"], "b" * 64)
        self.assertNotIn("approval_id", seen[0])
        self.dispatcher.execute.assert_called_once_with(
            "notes.read", {"path": "readme.txt"},
            approval_id="do-not-display-this-token",
        )

    def test_manifest_invocation_denial_does_not_execute(self):
        with patch.dict(os.environ, {"YUKI_ENV": "development"}):
            response = self.agent.invoke_manifest_skill(
                "notes.read", {"path": "readme.txt"}, confirm=lambda _: False,
            )
        self.assertFalse(response.ok)
        self.assertEqual(response.stage, "denied")
        self.dispatcher.execute.assert_not_called()

    def test_manifest_invocation_fails_closed_in_production(self):
        with patch.dict(os.environ, {"YUKI_ENV": "production"}):
            response = self.agent.invoke_manifest_skill(
                "notes.read", {"path": "readme.txt"}, confirm=lambda _: True,
            )
        self.assertFalse(response.ok)
        self.assertEqual(response.stage, "blocked")
        self.dispatcher.request_approval.assert_not_called()
        self.dispatcher.execute.assert_not_called()

    def test_missing_dispatcher_fails_closed(self):
        agent = YukiCoreAgent(skills=[TimeSkill()])
        with patch.dict(os.environ, {"YUKI_ENV": "development"}):
            response = agent.invoke_manifest_skill(
                "notes.read", {"path": "readme.txt"}, confirm=lambda _: True,
            )
        self.assertFalse(response.ok)
        self.assertEqual(response.stage, "blocked")


if __name__ == "__main__":
    unittest.main()
