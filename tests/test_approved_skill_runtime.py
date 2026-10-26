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
    def test_bounded_result_data_respects_serialized_json_budget(self):
        from core.guarded_skill_runtime import _bounded_result_data

        cases = [
            ({"text": "x" * 10_000}, 64),
            ({"text": "\\n\\t\\x00" * 100}, 80),
            ({"nested": [{"value": "😀" * 100}, {"value": 42}]}, 48),
            ({"a": True, "b": [1, 2, 3]}, 32),
        ]
        for value, limit in cases:
            with self.subTest(limit=limit, value_type=type(value).__name__):
                bounded, truncated = _bounded_result_data(value, limit)
                self.assertIsNotNone(bounded)
                self.assertLessEqual(
                    len(json.dumps(bounded, ensure_ascii=True, separators=(",", ":"))),
                    limit,
                )
                if len(json.dumps(value, ensure_ascii=True, separators=(",", ":"))) > limit:
                    self.assertTrue(truncated)

    def test_bounded_result_data_rejects_non_finite_and_unsupported_values(self):
        from core.guarded_skill_runtime import _bounded_result_data

        for value in ({"number": float("nan")}, {"object": object()}, {1: "non-string-key"}):
            with self.subTest(value=type(value).__name__):
                bounded, truncated = _bounded_result_data(value, 128)
                self.assertIsNone(bounded)
                self.assertTrue(truncated)

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
        result = self.runtime.invoke("weather", chr(0xD800))
        self.assertEqual(result.status, "invalid_argument")
        self.assertFalse(result.ok)


    def test_runtime_rejects_skill_identity_change_after_approval(self):
        from core.guarded_skill_runtime import OneShotSkillApprovals, invoke_registered_skill

        approved_skill = DemoSkill()
        replacement_skill = DemoSkill()

        class MutableRegistry:
            def get(self, name):
                return replacement_skill

        approvals = OneShotSkillApprovals()
        approvals.approve("weather", "safe-input")
        result = invoke_registered_skill(
            MutableRegistry(),
            "weather",
            "safe-input",
            approvals=approvals,
            expected_skill=approved_skill,
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.status, "skill_identity_changed")
        self.assertEqual(approvals.consume("weather", "safe-input"), False)


    def test_one_shot_approvals_enforce_pending_limit(self):
        from core.guarded_skill_runtime import OneShotSkillApprovals

        approvals = OneShotSkillApprovals(max_pending=2)
        approvals.approve("weather", "first")
        approvals.approve("weather", "first")  # Duplicate approval is idempotent.
        approvals.approve("weather", "second")
        with self.assertRaisesRegex(RuntimeError, "Pending skill approvals limit reached"):
            approvals.approve("weather", "third")
        self.assertTrue(approvals.consume("weather", "first"))
        approvals.approve("weather", "third")
        self.assertTrue(approvals.consume("weather", "third"))

    def test_one_shot_approvals_validate_pending_limit(self):
        from core.guarded_skill_runtime import OneShotSkillApprovals

        for invalid in (0, -1, True, 1.5, "10"):
            with self.subTest(max_pending=invalid):
                with self.assertRaises(ValueError):
                    OneShotSkillApprovals(max_pending=invalid)

    def test_facade_blocks_execute_mutation_after_approval(self):
        class HighRisk:
            name = "weather"
            risk = "high"
            requires_approval = True
            def execute(self, argument=""):
                return type("Result", (), {"ok": True, "message": "original"})()

        skill = HighRisk()
        self.registry.register(skill)
        self.runtime.approve_invocation("weather", "mutated-execute")
        skill.execute = lambda argument="": type(
            "Result", (), {"ok": True, "message": "mutated"} 
        )()

        result = self.runtime.invoke("weather", "mutated-execute")

        self.assertFalse(result.ok)
        self.assertEqual(result.status, "skill_execution_changed")
        self.assertEqual(self.events[-1], ("skill:invoke", "skill_execution_changed"))

    def test_facade_blocks_risk_metadata_mutation_after_approval(self):
        class HighRisk:
            name = "weather"
            risk = "high"
            requires_approval = True
            def execute(self, argument=""):
                return type("Result", (), {"ok": True, "message": "executed"})()

        skill = HighRisk()
        self.registry.register(skill)
        self.runtime.approve_invocation("weather", "mutated-risk")
        skill.risk = "low"
        skill.requires_approval = False

        result = self.runtime.invoke("weather", "mutated-risk")

        self.assertFalse(result.ok)
        self.assertEqual(result.status, "skill_execution_changed")
        self.assertEqual(self.events[-1], ("skill:invoke", "skill_execution_changed"))

    def test_facade_blocks_registry_replacement_after_approval(self):
        original = DemoSkill()
        replacement = DemoSkill()
        self.registry.register(original)
        self.runtime.approve_invocation("weather", "sunny")
        self.registry.items["weather"] = replacement

        result = self.runtime.invoke("weather", "sunny")

        self.assertFalse(result.ok)
        self.assertEqual(result.status, "skill_identity_changed")
        self.assertEqual(self.events[-1], ("skill:invoke", "skill_identity_changed"))

    def test_missing_or_failing_audit_sink_does_not_crash_approval_denial(self):
        from core.guarded_skill_runtime import OneShotSkillApprovals

        runtime_without_audit = ApprovedSkillRuntime(
            self.registry, self.runtime.approval_store, audit=None
        )
        self.registry.register(type("HighRisk", (), {
            "name": "weather", "risk": "high", "requires_approval": True
        })())
        result = runtime_without_audit.invoke("weather", "blocked")
        self.assertFalse(result.ok)
        self.assertEqual(result.status, "approval_required")

        def failing_audit(_event, _status):
            raise RuntimeError("audit unavailable")

        runtime_with_failing_audit = ApprovedSkillRuntime(
            self.registry,
            self.runtime.approval_store,
            invocation_approvals=OneShotSkillApprovals(),
            audit=failing_audit,
        )
        result = runtime_with_failing_audit.invoke("weather", "blocked")
        self.assertFalse(result.ok)
        self.assertEqual(result.status, "approval_required")

    def test_facade_rejects_high_risk_approval_without_instance_snapshot(self):
        class HighRisk:
            name = "weather"
            risk = "high"
            requires_approval = True
            def execute(self, argument=""):
                return type("Result", (), {"ok": True, "message": "executed"})()

        self.registry.register(HighRisk())
        # Bypassing the facade's approval method must not create an accepted
        # authorization because it has no captured skill-instance identity.
        self.runtime.invocation_approvals.approve("weather", "dangerous")

        result = self.runtime.invoke("weather", "dangerous")

        self.assertFalse(result.ok)
        self.assertEqual(result.status, "skill_identity_changed")


    def test_facade_does_not_let_concurrent_call_burn_in_flight_approval(self):
        import threading

        entered = threading.Event()
        release = threading.Event()
        class BlockingHighRisk:
            name = "weather"
            risk = "high"
            requires_approval = True
            def execute(self, argument=""):
                entered.set()
                if not release.wait(timeout=5):
                    raise TimeoutError("test release timed out")
                return type("Result", (), {"ok": True, "message": "executed"})()

        self.registry.register(BlockingHighRisk())
        self.runtime.approve_invocation("weather", "same-request")
        outcomes = []
        worker = threading.Thread(
            target=lambda: outcomes.append(self.runtime.invoke("weather", "same-request"))
        )
        worker.start()
        self.assertTrue(entered.wait(timeout=5))
        duplicate = self.runtime.invoke("weather", "same-request")
        self.assertEqual(duplicate.status, "approval_in_use")
        release.set()
        worker.join(timeout=5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(len(outcomes), 1)
        self.assertTrue(outcomes[0].ok)


    def test_cannot_mint_new_approval_while_same_request_is_in_flight(self):
        import threading

        entered = threading.Event()
        release = threading.Event()

        class BlockingHighRisk:
            name = "weather"
            risk = "high"
            requires_approval = True
            def execute(self, argument=""):
                entered.set()
                if not release.wait(timeout=5):
                    raise TimeoutError("test release timed out")
                return type("Result", (), {"ok": True, "message": "executed"})()

        self.registry.register(BlockingHighRisk())
        self.runtime.approve_invocation("weather", "same-request")
        outcomes = []
        worker = threading.Thread(
            target=lambda: outcomes.append(self.runtime.invoke("weather", "same-request"))
        )
        worker.start()
        self.assertTrue(entered.wait(timeout=5))
        with self.assertRaisesRegex(RuntimeError, "already in progress"):
            self.runtime.approve_invocation("weather", "same-request")
        duplicate = self.runtime.invoke("weather", "same-request")
        self.assertEqual(duplicate.status, "approval_in_use")
        release.set()
        worker.join(timeout=5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(len(outcomes), 1)
        self.assertTrue(outcomes[0].ok)
        replay = self.runtime.invoke("weather", "same-request")
        self.assertEqual(replay.status, "approval_required")

    def test_registry_lookup_exception_fails_closed(self):
        class UnavailableRegistry:
            def get(self, _name):
                raise RuntimeError("registry unavailable")

        runtime = ApprovedSkillRuntime(
            UnavailableRegistry(),
            self.store,
            audit=lambda action, status: self.events.append((action, status)),
        )
        result = runtime.invoke("weather", "safe-input")
        self.assertFalse(result.ok)
        self.assertEqual(result.status, "registry_failed")
        self.assertEqual(self.events[-1], ("skill:invoke", "registry_failed"))

    def test_invalid_invocation_is_rejected_before_registry_lookup(self):
        class MustNotBeCalledRegistry:
            def get(self, _name):
                raise AssertionError("invalid input must not query registry")

        runtime = ApprovedSkillRuntime(MustNotBeCalledRegistry(), self.store)
        for skill_name, argument, expected in (
            ("", "safe", "invalid_name"),
            ("weather", chr(0xD800), "invalid_argument"),
            ("weather", "x" * 20001, "invalid_argument"),
        ):
            with self.subTest(expected=expected, argument_length=len(argument)):
                result = runtime.invoke(skill_name, argument)
                self.assertFalse(result.ok)
                self.assertEqual(result.status, expected)


    def test_approve_invocation_rejects_invalid_input_before_registry_lookup(self):
        class MustNotBeCalledRegistry:
            def get(self, _name):
                raise AssertionError("invalid approval input must not query registry")

        runtime = ApprovedSkillRuntime(MustNotBeCalledRegistry(), self.store)
        for skill_name, argument in (
            ("", "safe"),
            ("weather", chr(0xD800)),
            ("weather", "x" * 20001),
        ):
            with self.subTest(skill_name=skill_name, argument_length=len(argument)):
                with self.assertRaises(ValueError):
                    runtime.approve_invocation(skill_name, argument)

    def test_approve_invocation_fails_closed_when_registry_is_unavailable(self):
        class UnavailableRegistry:
            def get(self, _name):
                raise RuntimeError("registry unavailable")

        runtime = ApprovedSkillRuntime(UnavailableRegistry(), self.store)
        with self.assertRaisesRegex(RuntimeError, "cannot approve invocation"):
            runtime.approve_invocation("weather", "safe-input")
        self.assertFalse(runtime.invocation_approvals.consume("weather", "safe-input"))

    def test_registry_failure_releases_facade_approval_token(self):
        class FlakyRegistry:
            def __init__(self):
                self.skill = None
                self.fail = False
            def get(self, _name):
                if self.fail:
                    raise RuntimeError("registry temporarily unavailable")
                return self.skill
            def register(self, skill):
                self.skill = skill

        registry = FlakyRegistry()
        registry.register(type("HighRisk", (), {
            "name": "weather",
            "risk": "high",
            "requires_approval": True,
            "execute": lambda self, argument="": type(
                "Result", (), {"ok": True, "message": "executed"}
            )(),
        })())
        runtime = ApprovedSkillRuntime(registry, self.store)
        runtime.approve_invocation("weather", "retry-me")
        registry.fail = True

        result = runtime.invoke("weather", "retry-me")

        self.assertFalse(result.ok)
        self.assertEqual(result.status, "registry_failed")
        self.assertFalse(
            runtime.invocation_approvals.consume("weather", "retry-me"),
            "registry failure must not strand a facade-bound approval token",
        )

    def test_low_risk_approval_is_consumed_without_leaking_pending_token(self):
        self.registry.register(DemoSkill())
        self.runtime.approve_invocation("weather", "low-risk")
        result = self.runtime.invoke("weather", "low-risk")
        self.assertTrue(result.ok)
        self.assertFalse(
            self.runtime.invocation_approvals.consume("weather", "low-risk"),
            "low-risk invocations must not leave an unused one-shot token behind",
        )

    def test_removed_skill_releases_facade_approval_token(self):
        class HighRisk:
            name = "weather"
            risk = "high"
            requires_approval = True
            def execute(self, argument=""):
                return type("Result", (), {"ok": True, "message": "executed"})()

        self.registry.register(HighRisk())
        self.runtime.approve_invocation("weather", "removed")
        self.registry.items.pop("weather")
        result = self.runtime.invoke("weather", "removed")
        self.assertFalse(result.ok)
        self.assertEqual(result.status, "not_found")
        self.assertFalse(
            self.runtime.invocation_approvals.consume("weather", "removed"),
            "a removed skill must not strand its approval token",
        )


    def test_facade_blocks_in_place_function_code_mutation_after_approval(self):
        class HighRisk:
            name = "weather"
            risk = "high"
            requires_approval = True

            def execute(self, argument=""):
                return type("Result", (), {"ok": True, "message": "original"})()

        self.registry.register(HighRisk())
        self.runtime.approve_invocation("weather", "mutated-code")
        original_function = HighRisk.execute

        def replacement(self, argument=""):
            return type("Result", (), {"ok": True, "message": "replacement"})()

        # Keep the function object identity stable while replacing its code.
        original_function.__code__ = replacement.__code__
        result = self.runtime.invoke("weather", "mutated-code")

        self.assertFalse(result.ok)
        self.assertEqual(result.status, "skill_execution_changed")

    def test_facade_blocks_callable_object_code_mutation_after_approval(self):
        class CallableTarget:
            def __call__(self, argument=""):
                return type("Result", (), {"ok": True, "message": "original"})()

        class HighRisk:
            name = "weather"
            risk = "high"
            requires_approval = True
            execute = CallableTarget()

        self.registry.register(HighRisk())
        self.runtime.approve_invocation("weather", "mutated-callable")
        original_call = CallableTarget.__call__

        def replacement(self, argument=""):
            return type("Result", (), {"ok": True, "message": "replacement"})()

        original_call.__code__ = replacement.__code__
        result = self.runtime.invoke("weather", "mutated-callable")

        self.assertFalse(result.ok)
        self.assertEqual(result.status, "skill_execution_changed")


if __name__ == "__main__":
    unittest.main()
