"""Tests for persistent approvals and approved skill wiring."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
import json

from core.approved_skill_wiring import ApprovedSkillWiringService, SkillApprovalStore, SkillWiringError
from skills.metadata_discovery import discover_skill_manifests


class Skill:
    name = "weather"
    def execute(self, argument=""):
        return argument


class Registry:
    def __init__(self):
        self.items = {}
    def register(self, skill):
        if skill.name in self.items:
            raise ValueError("duplicate")
        self.items[skill.name] = skill


class ApprovedSkillWiringTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.manifest_path = self.root / "weather.skill.json"
        self.manifest_path.write_text(json.dumps({
            "name": "weather", "description": "Weather lookup",
            "risk": "low", "permissions": ["network"], "entrypoint": "weather.py",
        }), encoding="utf-8")
        self.candidate = discover_skill_manifests(self.root)[0]
        self.db = self.root / "approvals.sqlite3"
        self.store = SkillApprovalStore(self.db)
        self.registry = Registry()
        self.service = ApprovedSkillWiringService(self.registry, self.store)

    def test_not_approved_candidate_is_not_wired(self):
        result = self.service.wire_approved((self.candidate,), {"weather": Skill()})
        self.assertEqual(result, ())
        self.assertEqual(self.registry.items, {})

    def test_approved_candidate_wires_only_supplied_trusted_instance(self):
        self.store.approve(self.candidate, approved_by="operator")
        result = self.service.wire_approved((self.candidate,), {"weather": Skill()})
        self.assertEqual(result, ("weather",))
        self.assertIn("weather", self.registry.items)

    def test_manifest_change_invalidates_approval(self):
        self.store.approve(self.candidate, approved_by="operator")
        self.manifest_path.write_text(self.manifest_path.read_text(encoding="utf-8") + " ", encoding="utf-8")
        changed = discover_skill_manifests(self.root)[0]
        self.assertFalse(self.store.is_approved(changed))
        self.assertEqual(self.service.wire_approved((changed,), {"weather": Skill()}), ())

    def test_revocation_prevents_future_wiring(self):
        self.store.approve(self.candidate, approved_by="operator")
        self.assertTrue(self.store.revoke(self.candidate.name, self.candidate.content_sha256))
        self.assertFalse(self.store.is_approved(self.candidate))
        self.assertEqual(self.service.wire_approved((self.candidate,), {"weather": Skill()}), ())

    def test_persistent_approval_survives_store_recreation(self):
        self.store.approve(self.candidate, approved_by="operator")
        self.assertTrue(SkillApprovalStore(self.db).is_approved(self.candidate))

    def test_missing_instance_never_imports_entrypoint(self):
        self.store.approve(self.candidate, approved_by="operator")
        self.assertEqual(self.service.wire_approved((self.candidate,), {}), ())


if __name__ == "__main__":
    unittest.main()
