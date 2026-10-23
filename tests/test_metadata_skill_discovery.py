"""Tests for metadata-only skill discovery and explicit registration."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from skills.metadata_discovery import (
    SkillDiscoveryError, discover_skill_manifests, register_approved_skill,
)


class DemoSkill:
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


class MetadataDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def manifest(self, name="weather", **overrides):
        data = {
            "name": name,
            "description": "Get a weather summary",
            "risk": "low",
            "permissions": ["network"],
            "entrypoint": "weather.py",
        }
        data.update(overrides)
        path = self.root / f"{name}.skill.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def test_discovers_metadata_without_importing_entrypoint(self):
        self.manifest()
        (self.root / "weather.py").write_text("raise RuntimeError('must not execute')", encoding="utf-8")
        candidates = discover_skill_manifests(self.root)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].name, "weather")
        self.assertEqual(candidates[0].status, "pending_review")

    def test_rejects_path_traversal_and_unknown_permissions(self):
        self.manifest(entrypoint="../evil.py")
        with self.assertRaises(SkillDiscoveryError):
            discover_skill_manifests(self.root)
        for path in self.root.iterdir():
            path.unlink()
        self.manifest(permissions=["root_access"])
        with self.assertRaises(SkillDiscoveryError):
            discover_skill_manifests(self.root)

    def test_requires_explicit_approval_and_unchanged_manifest(self):
        self.manifest()
        candidate = discover_skill_manifests(self.root)[0]
        registry = Registry()
        with self.assertRaises(SkillDiscoveryError):
            register_approved_skill(registry, candidate, DemoSkill(), approved_names=set(),
                                    verified_sha256=candidate.content_sha256)
        with self.assertRaises(SkillDiscoveryError):
            register_approved_skill(registry, candidate, DemoSkill(), approved_names={"weather"},
                                    verified_sha256="0" * 64)
        register_approved_skill(registry, candidate, DemoSkill(), approved_names={"weather"},
                                verified_sha256=candidate.content_sha256)
        self.assertIn("weather", registry.items)

    def test_duplicate_names_are_rejected(self):
        self.manifest("weather")
        self.manifest("weather2", name="weather")
        with self.assertRaises(SkillDiscoveryError):
            discover_skill_manifests(self.root)

    def test_rejects_invalid_manifest_shape(self):
        (self.root / "bad.skill.json").write_text("[]", encoding="utf-8")
        with self.assertRaises(SkillDiscoveryError):
            discover_skill_manifests(self.root)


if __name__ == "__main__":
    unittest.main()
