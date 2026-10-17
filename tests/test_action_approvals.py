import unittest

from core.action_approvals import ActionApprovalStore


class ActionApprovalStoreTests(unittest.TestCase):
    def setUp(self):
        self.store = ActionApprovalStore(ttl_seconds=60)
        self.action = {"operation": "file.write", "target": "notes/today.txt",
                       "arguments": {"content": "hello"}}

    def test_token_consumes_only_for_exact_action(self):
        issued = self.store.issue(self.action, actor="master", session_id="cli-1", approved=True)
        self.assertTrue(self.store.consume(issued.token, self.action, actor="master", session_id="cli-1"))
        self.assertFalse(self.store.consume(issued.token, self.action, actor="master", session_id="cli-1"))

    def test_token_cannot_be_replayed_for_changed_target_or_arguments(self):
        issued = self.store.issue(self.action, actor="master", session_id="cli-1", approved=True)
        changed = {**self.action, "target": "secrets.txt"}
        self.assertFalse(self.store.consume(issued.token, changed, actor="master", session_id="cli-1"))
        self.assertFalse(self.store.consume(issued.token, self.action, actor="master", session_id="cli-1"))

    def test_token_is_bound_to_actor_and_session(self):
        issued = self.store.issue(self.action, actor="master", session_id="cli-1", approved=True)
        self.assertFalse(self.store.consume(issued.token, self.action, actor="other", session_id="cli-1"))
        issued = self.store.issue(self.action, actor="master", session_id="cli-1", approved=True)
        self.assertFalse(self.store.consume(issued.token, self.action, actor="master", session_id="cli-2"))

    def test_denied_approval_cannot_issue_token(self):
        with self.assertRaises(PermissionError):
            self.store.issue(self.action, actor="master", session_id="cli-1", approved=False)

    def test_rejects_oversized_or_non_json_actions(self):
        with self.assertRaises(ValueError):
            self.store.issue({"bad": float("nan")}, actor="master", session_id="cli-1", approved=True)
        with self.assertRaises(ValueError):
            self.store.issue({"content": "x" * 17000}, actor="master", session_id="cli-1", approved=True)


if __name__ == "__main__":
    unittest.main()
