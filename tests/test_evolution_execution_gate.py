import tempfile
import unittest
from pathlib import Path

from automation.evolution_execution_gate import (
    EvolutionExecutionGate,
    EvolutionExecutionGateError,
)
from core.sandbox_policy import SandboxPolicy


class EvolutionExecutionGateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.workspace = self.root / "worktree"
        self.workspace.mkdir()
        self.calls = []
        self.gate = EvolutionExecutionGate(
            workspace_root=self.root,
            approval_verifier=self.verify,
        )

    def verify(self, **kwargs):
        self.calls.append(kwargs)
        return True

    def policy(self, *, network_mode="none", capabilities=frozenset({"write_workspace"})):
        return SandboxPolicy(network_mode=network_mode, capabilities=capabilities)

    def prepare(self, **overrides):
        values = {
            "plan_id": "plan-1",
            "request_id": "upgrade-1",
            "plan_digest": "a" * 64,
            "actor_id": "alice",
            "image": "python:3.11",
            "workspace": self.workspace,
            "command": ("python", "-m", "unittest"),
            "policy": self.policy(),
        }
        values.update(overrides)
        return self.gate.prepare(**values)

    def test_prepares_immutable_spec_only_after_exact_approval(self):
        spec = self.prepare()
        self.assertEqual(spec.workspace, str(self.workspace))
        self.assertEqual(spec.command, ("python", "-m", "unittest"))
        self.assertEqual(self.calls, [{
            "plan_id": "plan-1",
            "plan_digest": "a" * 64,
            "request_id": "upgrade-1",
            "actor_id": "alice",
        }])
        with self.assertRaises(AttributeError):
            spec.plan_id = "changed"

    def test_rejects_false_or_non_boolean_approval(self):
        for result in (False, None, 1, "yes"):
            gate = EvolutionExecutionGate(
                workspace_root=self.root,
                approval_verifier=lambda **kwargs: result,
            )
            with self.assertRaises(EvolutionExecutionGateError):
                gate.prepare(
                    plan_id="plan-1", request_id="upgrade-1", plan_digest="a" * 64,
                    actor_id="alice", image="python:3.11", workspace=self.workspace,
                    command=("python",), policy=self.policy(),
                )

    def test_approval_backend_errors_fail_closed(self):
        def broken(**kwargs):
            raise RuntimeError("backend details must not leak")
        gate = EvolutionExecutionGate(workspace_root=self.root, approval_verifier=broken)
        with self.assertRaisesRegex(EvolutionExecutionGateError, "failed closed"):
            gate.prepare(
                plan_id="plan-1", request_id="upgrade-1", plan_digest="a" * 64,
                actor_id="alice", image="python:3.11", workspace=self.workspace,
                command=("python",), policy=self.policy(),
            )

    def test_rejects_workspace_outside_root_and_root_itself(self):
        outside = Path(self.tmp.name).parent
        for workspace in (outside, self.root):
            with self.assertRaises(EvolutionExecutionGateError):
                self.prepare(workspace=workspace)
        self.assertEqual(self.calls, [])

    def test_rejects_network_and_missing_write_capability(self):
        with self.assertRaises(EvolutionExecutionGateError):
            self.prepare(policy=self.policy(network_mode="allowlist",
                                            capabilities=frozenset({"write_workspace", "network_allowlist"})))
        with self.assertRaises(EvolutionExecutionGateError):
            self.prepare(policy=self.policy(capabilities=frozenset()))
        self.assertEqual(self.calls, [])

    def test_rejects_malformed_plan_digest_and_identifiers(self):
        for kwargs in ({"plan_digest": "not-a-digest"}, {"plan_id": "../unsafe"},
                       {"request_id": " "}, {"actor_id": "bad\nactor"}):
            with self.assertRaises(EvolutionExecutionGateError):
                self.prepare(**kwargs)
        self.assertEqual(self.calls, [])

    def test_rejects_shell_unsafe_or_unbounded_command_arguments(self):
        for command in ((), ("python", "\x00bad"), ("x" * 4097,), (object(),)):
            with self.assertRaises(EvolutionExecutionGateError):
                self.prepare(command=command)
        self.assertEqual(self.calls, [])

    def test_rejects_invalid_image_and_policy(self):
        with self.assertRaises(EvolutionExecutionGateError):
            self.prepare(image="bad image")
        with self.assertRaises(EvolutionExecutionGateError):
            self.prepare(policy=object())
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()
