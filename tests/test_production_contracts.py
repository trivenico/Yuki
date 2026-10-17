"""Behavioral contract tests for production safety primitives.

These tests verify the helpers themselves; they do not claim that every helper is
wired into the live agent runtime.
"""
import json
import tempfile
import unittest
from pathlib import Path

from core.continuous_eval import EvalCase, run_case
from core.governance import Governance
from core.output_guard import OutputGuard
from core.agent import YukiCoreAgent
from core.permissions import PermissionGate
from skills.base import Skill, SkillResult
from core.self_healing import RecoveryStatus, SelfHealingManager
from core.telemetry import Telemetry


class TelemetryIntegrationTests(unittest.TestCase):
    def test_agent_skill_call_records_status_without_arguments(self):
        telemetry = Telemetry(enabled=True)
        agent = YukiCoreAgent(telemetry=telemetry)
        response = agent.run_skill("not-registered", "password=supersecret")
        self.assertFalse(response.ok)
        events = telemetry.snapshot()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["name"], "agent.run_skill")
        self.assertEqual(events[0]["attributes"]["result_stage"], "blocked")
        self.assertNotIn("supersecret", json.dumps(events))
        self.assertNotIn("password=", json.dumps(events))

    def test_telemetry_redacts_secrets_in_innocently_named_attributes(self):
        safe = Telemetry._attributes({"label": "api_key=supersecret"})
        self.assertNotIn("supersecret", json.dumps(safe))
        self.assertIn("[REDACTED]", safe["label"])

    def test_disabled_telemetry_does_not_record_events(self):
        telemetry = Telemetry(enabled=False)
        with telemetry.span("agent.run_skill", attributes={"skill_name": "test"}):
            pass
        self.assertEqual(telemetry.snapshot(), [])

    def test_telemetry_write_failure_does_not_break_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            telemetry = Telemetry(enabled=True, event_log=tmp)
            with telemetry.span("agent.run_skill", attributes={"skill_name": "test"}):
                pass
            self.assertEqual(len(telemetry.snapshot()), 1)


class OutputGuardContractTests(unittest.TestCase):
    def test_private_key_material_is_withheld(self):
        result = OutputGuard().check("-----BEGIN PRIVATE KEY-----\nexample\n-----END PRIVATE KEY-----")
        self.assertFalse(result.allowed)
        self.assertEqual(result.text, "[Output withheld by safety policy.]")
        self.assertIn("private_key_material", result.reasons)

    def test_credential_assignment_is_withheld(self):
        result = OutputGuard().check("api_key=supersecret")
        self.assertFalse(result.allowed)
        self.assertNotIn("supersecret", result.text)

    def test_long_output_is_truncated(self):
        result = OutputGuard(max_chars=10).check("x" * 30)
        self.assertTrue(result.allowed)
        self.assertIn("Output truncated", result.text)
        self.assertIn("output_too_long", result.reasons)



class RuntimeOutputGuardTests(unittest.TestCase):
    def test_runtime_withholds_credential_in_skill_message(self):
        class LeakySkill(Skill):
            name = "leaky_message"
            description = "Test-only skill"
            risk = "low"
            def run(self, argument=""):
                return SkillResult(True, "api_key=supersecret")

        agent = YukiCoreAgent(
            skills=[LeakySkill()],
            permission_gate=PermissionGate(lambda _request: True),
        )
        response = agent.run_skill("leaky_message")
        self.assertFalse(response.ok)
        self.assertNotIn("supersecret", response.message)
        self.assertIn("withheld", response.message.lower())

    def test_runtime_withholds_credential_in_structured_data(self):
        class LeakySkill(Skill):
            name = "leaky_data"
            description = "Test-only skill"
            risk = "low"
            def run(self, argument=""):
                return SkillResult(True, "Completed.", {"api_key": "supersecret"})

        agent = YukiCoreAgent(
            skills=[LeakySkill()],
            permission_gate=PermissionGate(lambda _request: True),
        )
        response = agent.run_skill("leaky_data")
        self.assertIsNone(response.data)
        self.assertNotIn("supersecret", response.message)


class GovernanceContractTests(unittest.TestCase):
    def test_missing_approver_fails_closed_and_is_audited(self):
        with tempfile.TemporaryDirectory() as tmp:
            audit = Path(tmp) / "audit.jsonl"
            decision = Governance(audit_path=audit).request(
                action_id="a-1", action="send_email", actor="test", reason="test request"
            )
            self.assertFalse(decision.approved)
            logged = json.loads(audit.read_text(encoding="utf-8").splitlines()[0])
            self.assertFalse(logged["approved"])
            self.assertEqual(logged["action_id"], "a-1")

    def test_approver_exception_fails_closed(self):
        def broken_approver(_action, _reason):
            raise RuntimeError("approver offline")
        decision = Governance(approver=broken_approver).request(
            action_id="a-2", action="delete_file", actor="test", reason="test request"
        )
        self.assertFalse(decision.approved)


class SelfHealingContractTests(unittest.TestCase):
    def test_recovery_without_approval_does_not_run_handler(self):
        ran = []
        manager = SelfHealingManager(approval=lambda _name, _reason: False)
        manager.register_recovery(
            "provider", lambda: ran.append(True), verify=lambda: True,
            cooldown=0, max_recoveries=1, approval_required=True,
        )
        result = manager.recover("provider")
        self.assertEqual(result.status, RecoveryStatus.DENIED)
        self.assertEqual(ran, [])

    def test_recovery_is_not_reported_successful_without_verification(self):
        ran = []
        manager = SelfHealingManager(approval=lambda _name, _reason: True)
        manager.register_recovery(
            "provider", lambda: ran.append(True), verify=lambda: False,
            cooldown=0, max_recoveries=1, approval_required=True,
        )
        result = manager.recover("provider")
        self.assertEqual(result.status, RecoveryStatus.UNVERIFIED)
        self.assertEqual(ran, [True])


class EvaluationContractTests(unittest.TestCase):
    def test_evaluator_must_return_explicit_boolean_status_when_required(self):
        case = EvalCase("contract-1", "permissions", "test", expected_ok=False)
        result = run_case(case, lambda _text: "denied")
        self.assertFalse(result.passed)
        self.assertTrue(any("expected an explicit ok status" in failure for failure in result.failures))

    def test_evaluator_rejects_non_boolean_status(self):
        case = EvalCase("contract-2", "permissions", "test", expected_ok=False)
        result = run_case(case, lambda _text: {"output": "denied", "ok": "false"})
        self.assertFalse(result.passed)
        self.assertIn("expected ok status to be a boolean", result.failures)


class AgentIdentityContractTests(unittest.TestCase):
    def test_signature_verifies_and_tampering_fails(self):
        from core.agent_identity import IdentityUnavailable, generate_identity, sign_payload, verify_envelope
        try:
            identity, private_key = generate_identity("test-agent")
        except IdentityUnavailable:
            self.skipTest("optional cryptography dependency is not installed")
        envelope = sign_payload(identity, private_key, {"action": "test", "nonce": "n-1"})
        self.assertTrue(verify_envelope(envelope, trusted_fingerprints={identity.fingerprint}))
        tampered = dict(envelope)
        tampered["payload"] = {"action": "delete", "nonce": "n-1"}
        self.assertFalse(verify_envelope(tampered, trusted_fingerprints={identity.fingerprint}))
        self.assertFalse(verify_envelope(envelope, trusted_fingerprints={"not-trusted"}))


if __name__ == "__main__":
    unittest.main()
