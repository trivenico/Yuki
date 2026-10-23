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

    def test_high_risk_skill_requires_one_shot_invocation_approval(self):
        class HighRisk:
            name = "weather"
            risk = "high"
            requires_approval = True
            def execute(self, argument=""):
                return "executed"
        # Register through the test registry to isolate invocation policy.
        self.registry.register(HighRisk())
        first = self.runtime.invoke("weather")
        self.assertEqual(first.status, "approval_required")
        self.runtime.approve_invocation("weather")
        second = self.runtime.invoke("weather")
        self.assertTrue(second.ok)
        third = self.runtime.invoke("weather")
        self.assertEqual(third.status, "approval_required")


if __name__ == "__main__":
    unittest.main()
