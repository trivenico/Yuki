"""Tests for the approved-skill runtime facade."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from core.approved_skill_runtime import ApprovedSkillRuntime
from core.approved_skill_wiring import SkillApprovalStore
from skills.metadata_discovery import discover_skill_manifests


class DemoSkill:
    name = "weather"
    risk = "low"
    requires_approval = False
    def execute(self, argument=""):
        return type("Result", (), {"ok": True, "message": "done:" + argument})()


class Registry:
    def __init__(self):
        self.items = {}
    def register(self, skill):
        if skill.name in self.items:
            raise ValueError("duplicate")
        self.items[skill.name] = skill
    def get(self, name):
        return self.items.get(name)


class ApprovedSkillRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "weather.skill.json").write_text(json.dumps({
            "name": "weather", "description": "Weather lookup",
            "risk": "low", "permissions": ["network"], "entrypoint": "weather.py",
        }), encoding="utf-8")
        self.candidate = discover_skill_manifests(self.root)[0]
        self.registry = Registry()
        self.store = SkillApprovalStore(self.root / "approvals.db")
        self.events = []
        self.runtime = ApprovedSkillRuntime(self.registry, self.store, audit=lambda a, s: self.events.append((a, s)))

    def test_only_approved_matching_risk_instance_is_wired(self):
        self.assertEqual(self.runtime.wire_approved((self.candidate,), {"weather": DemoSkill()}), ())
        self.store.approve(self.candidate, approved_by="operator")
        self.assertEqual(self.runtime.wire_approved((self.candidate,), {"weather": DemoSkill()}), ("weather",))

    def test_invocation_routes_through_guarded_runtime(self):
        self.store.approve(self.candidate, approved_by="operator")
        self.runtime.wire_approved((self.candidate,), {"weather": DemoSkill()})
        result = self.runtime.invoke("weather", "sunny")
        self.assertTrue(result.ok)
        self.assertEqual(result.message, "done:sunny")
        self.assertEqual(self.events[-1], ("skill:invoke", "completed"))

    def test_unknown_skill_cannot_be_approved_or_invoked(self):
        with self.assertRaises(ValueError):
            self.runtime.approve_invocation("not-registered")
        result = self.runtime.invoke("not-registered")
        self.assertFalse(result.ok)
        self.assertEqual(result.status, "not_found")

    def test_unknown_risk_fails_closed_and_requires_one_shot_approval(self):
        class UnknownRisk:
            name = "weather"
            risk = "custom-unreviewed-risk"
            requires_approval = False
            def execute(self, argument=""):
                return type("Result", (), {"ok": True, "message": "executed"})()
        self.registry.register(UnknownRisk())
        first = self.runtime.invoke("weather")
        self.assertEqual(first.status, "approval_required")
        self.runtime.approve_invocation("weather")
        second = self.runtime.invoke("weather")
        self.assertTrue(second.ok)
        third = self.runtime.invoke("weather")
        self.assertEqual(third.status, "approval_required")

    def test_high_risk_skill_requires_one_shot_invocation_approval(self):
        class HighRisk:
            name = "weather"
            risk = "high"
            requires_approval = True
            def execute(self, argument=""):
                return type("Result", (), {"ok": True, "message": "executed"})()
        # Register through the test registry to isolate invocation policy.
        self.registry.register(HighRisk())
        first = self.runtime.invoke("weather")
        self.assertEqual(first.status, "approval_required")
        self.runtime.approve_invocation("weather")
        second = self.runtime.invoke("weather")
        self.assertTrue(second.ok)
        third = self.runtime.invoke("weather")
        self.assertEqual(third.status, "approval_required")

    def test_structured_result_data_is_bounded(self):
        from core.guarded_skill_runtime import OneShotSkillApprovals, invoke_registered_skill

        class LargeResultSkill:
            name = "large"
            risk = "low"
            requires_approval = False
            def execute(self, _argument=""):
                return {"ok": True, "message": "done", "payload": "x" * 100_000}

        registry = Registry()
        registry.register(LargeResultSkill())
        events = []
        result = invoke_registered_skill(
            registry, "large", approvals=OneShotSkillApprovals(),
            audit=lambda action, status: events.append((action, status)),
            max_result_chars=64,
        )
        self.assertTrue(result.ok)
        self.assertLessEqual(len(result.data["payload"]), 64)
        self.assertIn(("skill:invoke", "result_data_truncated"), events)

    def test_non_json_result_data_is_not_returned(self):
        from core.guarded_skill_runtime import OneShotSkillApprovals, invoke_registered_skill

        class InvalidDataSkill:
            name = "invalid-data"
            risk = "low"
            requires_approval = False
            def execute(self, _argument=""):
                return {"ok": True, "message": "done", "payload": object()}

        registry = Registry()
        registry.register(InvalidDataSkill())
        result = invoke_registered_skill(
            registry, "invalid-data", approvals=OneShotSkillApprovals(),
        )
        self.assertTrue(result.ok)
        self.assertIsNone(result.data)


    def test_approval_is_bound_to_exact_argument(self):
        class HighRisk:
            name = "weather"
            risk = "high"
            requires_approval = True
            def execute(self, argument=""):
                return type("Result", (), {"ok": True, "message": "executed:" + argument})()
        self.registry.register(HighRisk())
        self.runtime.approve_invocation("weather", "approved-input")
        changed = self.runtime.invoke("weather", "changed-input")
        self.assertEqual(changed.status, "approval_required")
        exact = self.runtime.invoke("weather", "approved-input")
        self.assertTrue(exact.ok)
        self.assertEqual(exact.message, "executed:approved-input")
        replay = self.runtime.invoke("weather", "approved-input")
        self.assertEqual(replay.status, "approval_required")

    def test_one_shot_approval_consumption_is_atomic_for_exact_argument(self):
        import threading
        from core.guarded_skill_runtime import OneShotSkillApprovals

        approvals = OneShotSkillApprovals()
        approvals.approve("weather", "same-input")
        barrier = threading.Barrier(3)
        outcomes = []
        lock = threading.Lock()

        def consume():
            barrier.wait()
            outcome = approvals.consume("weather", "same-input")
            with lock:
                outcomes.append(outcome)

        threads = [threading.Thread(target=consume) for _ in range(2)]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join(timeout=5)
        self.assertTrue(all(not thread.is_alive() for thread in threads))
        self.assertEqual(outcomes.count(True), 1)
        self.assertEqual(outcomes.count(False), 1)

    def test_invalid_utf8_argument_is_rejected_without_exception(self):
        self.registry.register(DemoSkill())
        result = self.runtime.invoke("weather", "\\ud800")
        self.assertEqual(result.status, "invalid_argument")
        self.assertFalse(result.ok)


if __name__ == "__main__":
    unittest.main()
