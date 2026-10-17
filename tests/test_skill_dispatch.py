import json
import sys
import unittest

from core.action_approvals import ActionApprovalStore
from core.skill_dispatch import SkillDispatcher
from core.skill_manifest import SkillManifest
from core.subprocess_executor import SubprocessExecutor


def make_manifest(**overrides):
    value = {
        "skill_id": "notes.append",
        "version": "1.0.0",
        "entrypoint": "builtins.notes_append",
        "input_schema": {"type": "object", "properties": {"text": {"type": "string", "maxLength": 100}},
                         "required": ["text"], "additionalProperties": False},
        "output_schema": {"type": "object", "properties": {"saved": {"type": "boolean"}},
                          "required": ["saved"], "additionalProperties": False},
        "capabilities": ["workspace.write"],
        "risk": "low",
        "timeout_seconds": 5,
        "max_output_bytes": 1024,
        "parallel_safe": False,
    }
    value.update(overrides)
    return SkillManifest.from_dict(value)


class SkillDispatcherTests(unittest.TestCase):
    def setUp(self):
        self.store = ActionApprovalStore()
        self.dispatcher = SkillDispatcher(capability_grants={"workspace.write", "workspace.read"},
                                          approval_store=self.store)
        self.dispatcher.register(make_manifest(), lambda args: {"saved": True})

    def test_subprocess_registration_runs_validated_handler_through_worker(self):
        manifest = make_manifest(skill_id="notes.worker", capabilities=["workspace.read"], risk="low")
        dispatcher = SkillDispatcher(capability_grants={"workspace.read"})
        script = (
            "import json,sys; req=json.load(sys.stdin); "
            "print(json.dumps({'ok':True,'result':{'output':{'saved':True}}}))"
        )
        dispatcher.register_subprocess(manifest, [sys.executable, "-c", script],
                                        executor=SubprocessExecutor(timeout_seconds=3))
        result = dispatcher.dispatch("notes.worker", {"text": "hello"})
        self.assertTrue(result.ok)
        self.assertEqual(result.output, {"saved": True})

    def test_subprocess_failure_does_not_expose_worker_diagnostics(self):
        manifest = make_manifest(skill_id="notes.worker", capabilities=["workspace.read"], risk="low")
        dispatcher = SkillDispatcher(capability_grants={"workspace.read"})
        script = "import sys; sys.stderr.write('token=private-secret'); sys.exit(2)"
        dispatcher.register_subprocess(manifest, [sys.executable, "-c", script],
                                        executor=SubprocessExecutor(timeout_seconds=3))
        result = dispatcher.dispatch("notes.worker", {"text": "hello"})
        self.assertFalse(result.ok)
        self.assertEqual(result.code, "handler_failed")
        self.assertNotIn("private-secret", str(result))

    def test_denies_unregistered_skill(self):
        self.assertEqual(self.dispatcher.dispatch("missing", {}).code, "skill_not_registered")

    def test_denies_missing_capability_grant(self):
        dispatcher = SkillDispatcher(capability_grants={"workspace.read"})
        dispatcher.register(make_manifest(), lambda args: {"saved": True})
        self.assertEqual(dispatcher.dispatch("notes.append", {"text": "hello"}).code, "capability_denied")

    def test_validates_input_before_handler(self):
        called = []
        dispatcher = SkillDispatcher(capability_grants={"workspace.write"}, approval_store=self.store)
        dispatcher.register(make_manifest(), lambda args: called.append(args) or {"saved": True})
        result = dispatcher.dispatch("notes.append", {"text": "hello", "extra": "no"})
        self.assertEqual(result.code, "invalid_input")
        self.assertEqual(called, [])

    def test_side_effecting_skill_requires_one_time_approval_token(self):
        called = []
        dispatcher = SkillDispatcher(capability_grants={"workspace.write"}, approval_store=self.store)
        dispatcher.register(make_manifest(), lambda args: called.append(args) or {"saved": True})
        result = dispatcher.dispatch("notes.append", {"text": "hello"})
        self.assertEqual(result.code, "approval_token_required")
        self.assertEqual(called, [])
        token = dispatcher.issue_approval("notes.append", {"text": "hello"},
                                           actor="master", session_id="cli-1", approved=True)
        result = dispatcher.dispatch("notes.append", {"text": "hello"}, approval_token=token.token,
                                     actor="master", session_id="cli-1")
        self.assertTrue(result.ok)
        self.assertEqual(len(called), 1)
        replay = dispatcher.dispatch("notes.append", {"text": "hello"}, approval_token=token.token,
                                     actor="master", session_id="cli-1")
        self.assertEqual(replay.code, "approval_invalid_or_expired")

    def test_approval_is_bound_to_arguments_actor_and_session(self):
        token = self.dispatcher.issue_approval("notes.append", {"text": "approved"},
                                               actor="master", session_id="cli-1", approved=True)
        result = self.dispatcher.dispatch("notes.append", {"text": "different"}, approval_token=token.token,
                                          actor="master", session_id="cli-1")
        self.assertEqual(result.code, "approval_invalid_or_expired")
        token = self.dispatcher.issue_approval("notes.append", {"text": "approved"},
                                               actor="master", session_id="cli-1", approved=True)
        result = self.dispatcher.dispatch("notes.append", {"text": "approved"}, approval_token=token.token,
                                          actor="other", session_id="cli-1")
        self.assertEqual(result.code, "approval_invalid_or_expired")

    def test_denied_approval_cannot_issue_token(self):
        with self.assertRaises(PermissionError):
            self.dispatcher.issue_approval("notes.append", {"text": "hello"},
                                           actor="master", session_id="cli-1", approved=False)

    def test_validated_output_returned_for_read_only_medium_risk_skill(self):
        manifest = make_manifest(skill_id="notes.read", capabilities=["workspace.read"], risk="medium")
        dispatcher = SkillDispatcher(capability_grants={"workspace.read"}, approval_store=self.store)
        dispatcher.register(manifest, lambda args: {"saved": True})
        token = dispatcher.issue_approval("notes.read", {"text": "hello"},
                                          actor="master", session_id="cli-1", approved=True)
        result = dispatcher.dispatch("notes.read", {"text": "hello"}, approval_token=token.token,
                                     actor="master", session_id="cli-1")
        self.assertTrue(result.ok)
        self.assertEqual(result.output, {"saved": True})

    def test_rejects_invalid_handler_output(self):
        manifest = make_manifest(skill_id="notes.read", capabilities=["workspace.read"], risk="medium")
        dispatcher = SkillDispatcher(capability_grants={"workspace.read"}, approval_store=self.store)
        dispatcher.register(manifest, lambda args: {"saved": "yes"})
        token = dispatcher.issue_approval("notes.read", {"text": "hello"},
                                          actor="master", session_id="cli-1", approved=True)
        self.assertEqual(dispatcher.dispatch("notes.read", {"text": "hello"}, approval_token=token.token,
                                             actor="master", session_id="cli-1").code, "invalid_output")


if __name__ == "__main__":
    unittest.main()
