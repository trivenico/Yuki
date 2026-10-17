import unittest

from core.skill_dispatch import SkillDispatcher
from core.skill_manifest import SkillManifest


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
    def test_denies_unregistered_skill(self):
        dispatcher = SkillDispatcher(capability_grants={"workspace.write"})
        self.assertEqual(dispatcher.dispatch("missing", {}).code, "skill_not_registered")

    def test_denies_missing_capability_grant(self):
        dispatcher = SkillDispatcher(capability_grants={"workspace.read"})
        dispatcher.register(make_manifest(), lambda args: {"saved": True})
        self.assertEqual(dispatcher.dispatch("notes.append", {"text": "hello"}).code, "capability_denied")

    def test_validates_input_before_handler(self):
        called = []
        dispatcher = SkillDispatcher(capability_grants={"workspace.write"})
        dispatcher.register(make_manifest(), lambda args: called.append(args) or {"saved": True})
        result = dispatcher.dispatch("notes.append", {"text": "hello", "extra": "no"})
        self.assertEqual(result.code, "invalid_input")
        self.assertEqual(called, [])

    def test_side_effecting_skill_requires_approval_even_if_risk_misclassified_low(self):
        called = []
        dispatcher = SkillDispatcher(capability_grants={"workspace.write"})
        dispatcher.register(make_manifest(), lambda args: called.append(args) or {"saved": True})
        result = dispatcher.dispatch("notes.append", {"text": "hello"})
        self.assertEqual(result.code, "approval_required")
        self.assertEqual(called, [])

    def test_approval_receives_exact_action_and_can_deny(self):
        seen = []
        dispatcher = SkillDispatcher(capability_grants={"workspace.write"},
                                      approval=lambda risk, action: seen.append((risk, action)) or False)
        dispatcher.register(make_manifest(), lambda args: {"saved": True})
        result = dispatcher.dispatch("notes.append", {"text": "hello"})
        self.assertEqual(result.code, "approval_denied")
        self.assertEqual(seen[0][1]["arguments"], {"text": "hello"})

    def test_validated_output_returned(self):
        dispatcher = SkillDispatcher(capability_grants={"workspace.read"},
                                      approval=lambda risk, action: True)
        manifest = make_manifest(skill_id="notes.read", capabilities=["workspace.read"], risk="medium")
        dispatcher.register(manifest, lambda args: {"saved": True})
        result = dispatcher.dispatch("notes.read", {"text": "hello"})
        self.assertTrue(result.ok)
        self.assertEqual(result.output, {"saved": True})

    def test_rejects_invalid_handler_output(self):
        dispatcher = SkillDispatcher(capability_grants={"workspace.read"},
                                      approval=lambda risk, action: True)
        manifest = make_manifest(skill_id="notes.read", capabilities=["workspace.read"], risk="medium")
        dispatcher.register(manifest, lambda args: {"saved": "yes"})
        self.assertEqual(dispatcher.dispatch("notes.read", {"text": "hello"}).code, "invalid_output")


if __name__ == "__main__":
    unittest.main()
