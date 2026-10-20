"""Regression tests for small, security-sensitive hardening primitives."""

import json
import unittest
from unittest.mock import patch

from core.action_approvals import ActionApprovalStore
from core.capabilities import capability_inventory, capability_inventory_json
from core.permissions import ApprovalRequest, PermissionGate
from core.skill_manifest import SkillManifest
from core.subprocess_executor import SubprocessExecutor
from skills.discovery import discover_skill_classes
from skills.health_skill import HealthSkill
from skills.knowledge_skill import KnowledgeSkill
from skills.image_skill import ImageGenerationSkill


class HardeningPrimitiveTests(unittest.TestCase):
    def test_action_approval_snapshots_arguments_and_is_one_time(self):
        store = ActionApprovalStore()
        arguments = {"nested": {"value": 1}}
        item = store.create("demo", arguments)
        arguments["nested"]["value"] = 2
        self.assertEqual(item.arguments["nested"]["value"], 1)
        self.assertEqual(store.consume(item.approval_id), item)
        self.assertIsNone(store.consume(item.approval_id))

    def test_action_approval_rejects_non_json_and_nan(self):
        store = ActionApprovalStore()
        for arguments in ({"bad": object()}, {"bad": float("nan")}):
            with self.subTest(arguments=type(arguments["bad"]).__name__):
                with self.assertRaises(ValueError):
                    store.create("demo", arguments)

    def test_action_approval_capacity_and_deny(self):
        store = ActionApprovalStore(max_pending=1)
        item = store.create("demo", {})
        with self.assertRaises(RuntimeError):
            store.create("other", {})
        self.assertTrue(store.deny(item.approval_id))
        self.assertFalse(store.deny(item.approval_id))

    def test_action_approval_validates_limits_and_input(self):
        for limit in (True, 0, 4097):
            with self.assertRaises(ValueError):
                ActionApprovalStore(max_pending=limit)
        store = ActionApprovalStore()
        for skill, args in (("", {}), (None, {}), ("demo", [])):
            with self.assertRaises(ValueError):
                store.create(skill, args)

    def test_permission_gate_fails_closed_for_invalid_or_missing_approver(self):
        self.assertFalse(PermissionGate().request("run", "needed"))
        gate = PermissionGate(lambda _: True)
        for args in (("", "reason", "low"), ("run", "", "low"), ("run", "reason", "critical")):
            self.assertFalse(gate.request(*args))

    def test_permission_gate_only_accepts_literal_true_and_handles_exceptions(self):
        self.assertFalse(PermissionGate(lambda _: 1).request("run", "reason"))
        self.assertFalse(PermissionGate(lambda _: (_ for _ in ()).throw(RuntimeError())).request("run", "reason"))
        seen = []
        gate = PermissionGate(lambda request: seen.append(request) or True)
        self.assertTrue(gate.request("delete", "user approved", "high"))
        self.assertEqual(seen, [ApprovalRequest("delete", "user approved", "high")])

    def test_capability_inventory_filters_and_sorts_without_execution(self):
        class Registry:
            def describe(self):
                return [
                    {"name": "z", "description": "x" * 600, "input_schema": []},
                    None,
                    {"name": "", "risk": "low"},
                    {"name": "a", "requires_approval": False, "input_schema": {"type": "object"}},
                ]

        result = capability_inventory(Registry())
        self.assertEqual([item["name"] for item in result], ["a", "z"])
        self.assertFalse(result[0]["requires_approval"])
        self.assertEqual(len(result[1]["description"]), 500)
        self.assertEqual(result[1]["input_schema"]["type"], "string")
        self.assertEqual(json.loads(capability_inventory_json(Registry())), result)

    def test_skill_manifest_accepts_valid_object_and_checks_schema(self):
        manifest = SkillManifest.from_dict({
            "skill_id": "demo.skill",
            "version": "1.2.3",
            "entrypoint": "package.worker",
            "input_schema": {
                "type": "object", "additionalProperties": False,
                "properties": {"name": {"type": "string", "maxLength": 5}},
                "required": ["name"],
            },
            "output_schema": {"type": "object", "additionalProperties": False, "properties": {}},
            "capabilities": ["read"],
            "risk": "low",
            "timeout_seconds": 5,
            "max_output_bytes": 1024,
        })
        self.assertEqual(manifest.skill_id, "demo.skill")
        self.assertEqual(SkillManifest.validate_object({"name": "Yuki"}, manifest.input_schema), {"name": "Yuki"})
        for value in ([], {"name": "toolong"}, {"name": "ok", "extra": 1}, {}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                SkillManifest.validate_object(value, manifest.input_schema)

    def test_skill_manifest_rejects_unsafe_metadata(self):
        base = {
            "skill_id": "demo", "version": "1.0.0", "entrypoint": "worker.main",
            "input_schema": {"type": "object", "additionalProperties": False, "properties": {}},
            "output_schema": {"type": "object", "additionalProperties": False, "properties": {}},
            "capabilities": [], "risk": "low", "timeout_seconds": 5, "max_output_bytes": 1024,
        }
        variants = [
            {"skill_id": "../bad"}, {"version": "latest"}, {"entrypoint": "worker;os.system"},
            {"risk": "critical"}, {"timeout_seconds": True}, {"max_output_bytes": 0},
            {"capabilities": ["bad capability"]},
            {"input_schema": {"type": "object", "additionalProperties": True, "properties": {}}},
        ]
        for change in variants:
            with self.subTest(change=change), self.assertRaises(ValueError):
                SkillManifest.from_dict({**base, **change})

    def test_subprocess_executor_validates_limits_and_command(self):
        for kwargs in ({"timeout_seconds": 0}, {"timeout_seconds": 61}, {"max_output_bytes": 0}):
            with self.assertRaises(ValueError):
                SubprocessExecutor(**kwargs)
        executor = SubprocessExecutor()
        for command in ([], ["python", ""], "python -c pass"):
            with self.assertRaises(ValueError):
                executor.execute(command, {})

    def test_subprocess_executor_json_round_trip_without_shell(self):
        executor = SubprocessExecutor()
        result = executor.execute(
            ["python", "-c", "import json,sys; print(json.dumps(json.load(sys.stdin)))"],
            {"value": "safe"},
        )
        self.assertEqual(result, {"value": "safe"})

    def test_subprocess_executor_rejects_bad_worker_results(self):
        executor = SubprocessExecutor()
        cases = [
            (["python", "-c", "print('not-json')"], ValueError),
            (["python", "-c", "print('[]')"], ValueError),
            (["python", "-c", "raise SystemExit(2)"], RuntimeError),
            (["python", "-c", "print('x' * 100)"], ValueError),
        ]
        for command, error in cases:
            with self.subTest(command=command), self.assertRaises(error):
                executor.execute(command, {}, max_output_bytes=20)

    def test_subprocess_executor_timeout_is_bounded(self):
        executor = SubprocessExecutor(timeout_seconds=0.1)
        with self.assertRaises(TimeoutError):
            executor.execute(["python", "-c", "import time; time.sleep(2)"], {})

    def test_local_skills_validate_inputs_without_external_calls(self):
        with unittest.mock.patch("companion.health_tracker.HealthTracker.add", return_value={"id": "1"}):
            health = HealthSkill("/tmp/yuki-test")
            self.assertFalse(health.run("").ok)
            self.assertFalse(health.run("add sleep").ok)
            self.assertTrue(health.run("add sleep | 8 hours").ok)
            self.assertTrue(health.run("list").ok)
        with unittest.mock.patch("knowledge.knowledge_graph.KnowledgeGraph.add_entity", return_value={"id": "1"}):
            knowledge = KnowledgeSkill("/tmp/yuki-test")
            self.assertFalse(knowledge.run("").ok)
            self.assertFalse(knowledge.run("unknown").ok)
            self.assertTrue(knowledge.run("add person | Ada | engineer").ok)
            self.assertTrue(knowledge.run("search Ada").ok)
            self.assertTrue(knowledge.run("list").ok)

    def test_remote_image_skill_is_opt_in_and_never_calls_provider_without_key(self):
        skill = ImageGenerationSkill()
        with patch.dict("os.environ", {}, clear=True):
            self.assertFalse(skill.run("").ok)
            result = skill.run("draw a tree")
        self.assertFalse(result.ok)
        self.assertIn("OPENAI_API_KEY", result.message)

    def test_discovery_reports_package_and_returns_skill_classes(self):
        result = discover_skill_classes("skills", excluded={"base", "registry", "discovery", "__init__"})
        self.assertIn("classes", result)
        self.assertIn("failures", result)
        self.assertTrue(any(getattr(cls, "name", None) == "health_log" for cls in result["classes"]))


if __name__ == "__main__":
    unittest.main()
