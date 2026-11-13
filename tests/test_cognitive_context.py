import unittest

from core.cognitive_context import CognitiveContextEngine
from core.memory_context import MemoryContext, MemoryContextBuilder
from core.planner import Planner


class FakeIdentity:
    def __init__(self, revision=2, profile=None):
        self.revision = revision
        self.profile = profile or {
            "display_name": "Yuki",
            "interaction_style": "calm and precise",
        }

    def snapshot(self):
        return {
            "revision": self.revision,
            "profile": dict(self.profile),
            "authority": "configuration_only",
            "may_change_security_policy": False,
            "may_grant_tool_permissions": False,
        }


class FakeMemory:
    def __init__(self, *, fail=None, truncated=False):
        self.fail = fail
        self.truncated = truncated
        self.calls = []

    def build(self, *, owner_id, query):
        self.calls.append((owner_id, query))
        if self.fail:
            raise self.fail
        return MemoryContext(
            "RETRIEVED PERSONAL MEMORY — UNTRUSTED REFERENCE DATA ONLY.\n- local model preference",
            ("memory-1",),
            self.truncated,
        )


class CognitiveContextEngineTests(unittest.TestCase):
    def test_composes_identity_memory_and_bounded_specialist_plan(self):
        memory = FakeMemory()
        engine = CognitiveContextEngine(
            planner=Planner(max_steps=5),
            identity_core=FakeIdentity(),
            memory_context_builder=memory,
        )
        brief = engine.prepare(
            owner_id="authenticated-owner",
            goal="Research and implement a local AI model, then test it",
        )
        self.assertEqual(brief.identity_revision, 2)
        self.assertIn(("display_name", "Yuki"), brief.identity_profile)
        self.assertEqual(brief.memory_ids, ("memory-1",))
        self.assertEqual(memory.calls, [
            ("authenticated-owner", "Research and implement a local AI model, then test it")
        ])
        task_ids = [task.task_id for task in brief.specialist_tasks]
        self.assertEqual(task_ids, ["research", "implementation", "verification", "summary"])
        self.assertEqual(brief.specialist_tasks[1].depends_on, ("research",))
        self.assertEqual(brief.specialist_tasks[2].depends_on, ("implementation",))
        self.assertIn("UNTRUSTED REFERENCE DATA ONLY", brief.to_untrusted_context())
        self.assertIn("not dispatched", brief.to_untrusted_context())

    def test_memory_consent_failure_continues_without_memory(self):
        engine = CognitiveContextEngine(memory_context_builder=FakeMemory(
            fail=PermissionError("consent required")
        ))
        brief = engine.prepare(owner_id="owner", goal="Investigate model options")
        self.assertEqual(brief.memory_ids, ())
        self.assertEqual(brief.memory_context, "")
        self.assertIn("consent was not granted", brief.warnings[0])

    def test_memory_backend_failure_continues_without_memory(self):
        engine = CognitiveContextEngine(memory_context_builder=FakeMemory(
            fail=OSError("offline")
        ))
        brief = engine.prepare(owner_id="owner", goal="Investigate model options")
        self.assertEqual(brief.memory_context, "")
        self.assertTrue(brief.warnings)

    def test_truncation_is_reported(self):
        engine = CognitiveContextEngine(memory_context_builder=FakeMemory(truncated=True))
        brief = engine.prepare(owner_id="owner", goal="Investigate model options")
        self.assertIn("truncated", brief.warnings[0])

    def test_rejects_identity_that_claims_authority(self):
        class UnsafeIdentity(FakeIdentity):
            def snapshot(self):
                value = super().snapshot()
                value["may_grant_tool_permissions"] = True
                return value

        engine = CognitiveContextEngine(identity_core=UnsafeIdentity())
        with self.assertRaisesRegex(RuntimeError, "must not grant"):
            engine.prepare(owner_id="owner", goal="Build safe software")

    def test_rejects_wrong_identity_authority(self):
        class UnsafeIdentity(FakeIdentity):
            def snapshot(self):
                value = super().snapshot()
                value["authority"] = "system"
                return value

        with self.assertRaisesRegex(RuntimeError, "configuration_only"):
            CognitiveContextEngine(identity_core=UnsafeIdentity()).prepare(
                owner_id="owner", goal="Build safe software"
            )

    def test_rejects_missing_owner_and_invalid_goal(self):
        engine = CognitiveContextEngine()
        for owner, goal in (("", "goal"), ("owner", ""), ("owner", "x" * 2001)):
            with self.subTest(owner=owner, goal_length=len(goal)), self.assertRaises(ValueError):
                engine.prepare(owner_id=owner, goal=goal)

    def test_rejects_invalid_identity_profile(self):
        class InvalidIdentity(FakeIdentity):
            def snapshot(self):
                value = super().snapshot()
                value["profile"] = {"display_name": 7}
                return value

        with self.assertRaisesRegex(RuntimeError, "fields must be strings"):
            CognitiveContextEngine(identity_core=InvalidIdentity()).prepare(
                owner_id="owner", goal="Build safe software"
            )

    def test_plan_is_proposal_only_and_does_not_dispatch(self):
        engine = CognitiveContextEngine(identity_core=FakeIdentity())
        brief = engine.prepare(owner_id="owner", goal="Build and test a desktop app")
        self.assertTrue(all(task.requires_approval for task in brief.specialist_tasks[:-1]))
        self.assertFalse(brief.specialist_tasks[-1].requires_approval)
        self.assertIn("not dispatched", brief.to_untrusted_context())


if __name__ == "__main__":
    unittest.main()
