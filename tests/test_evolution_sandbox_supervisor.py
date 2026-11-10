import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock

from automation.evolution_execution_gate import SandboxExecutionSpec
from automation.evolution_sandbox_supervisor import (
    EvolutionSandboxError,
    EvolutionSandboxSupervisor,
)
from core.sandbox_policy import SandboxPolicy


class FakeStream:
    def __init__(self, chunks=()):
        self.chunks = list(chunks)
        self.closed = False
    def read(self, size=-1):
        return self.chunks.pop(0) if self.chunks else b""
    def close(self):
        self.closed = True


class FakeProcess:
    def __init__(self, code=0, stdout=(), stderr=()):
        self.pid = 987654
        self.returncode = code
        self.stdout = FakeStream(stdout)
        self.stderr = FakeStream(stderr)
        self._code = code
        self.terminated = False
    def poll(self):
        return self.returncode
    def wait(self, timeout=None):
        self.returncode = self._code
        return self.returncode
    def terminate(self):
        self.terminated = True
        self.returncode = -15
    def kill(self):
        self.returncode = -9


class SupervisorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.workspace = self.root / "task"
        self.workspace.mkdir()
        self.policy = SandboxPolicy(
            network_mode="none",
            capabilities=frozenset({"read_workspace", "write_workspace"}),
            timeout_seconds=2, max_output_bytes=64,
        )
        self.spec = SandboxExecutionSpec(
            plan_id="plan-1", request_id="request-1", plan_digest="a" * 64,
            actor_id="actor", image="registry.example/yuki@sha256:" + "b" * 64,
            workspace=str(self.workspace), command=("python", "-m", "unittest"),
            policy=self.policy,
        )
        self.process = FakeProcess(stdout=[b"ok"], stderr=[b""])
        self.argv = ["docker", "run", "--rm", "--network", "none", self.spec.image, "python"]
        self.builder = Mock(return_value=self.argv)
        self.popen = Mock(return_value=self.process)
        self.supervisor = EvolutionSandboxSupervisor(
            workspace_root=self.root, popen_factory=self.popen, argv_builder=self.builder,
        )

    def test_runs_pinned_image_and_returns_bounded_result(self):
        result = self.supervisor.execute(self.spec)
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.stdout, "ok")
        self.assertFalse(result.timed_out)
        argv = self.popen.call_args.args[0]
        self.assertIn("--pull=never", argv)
        self.assertIn("--cidfile", argv)

    def test_rejects_tag_only_image_before_start(self):
        bad = SandboxExecutionSpec(
            **{**self.spec.__dict__, "image": "python:3.12"}
        )
        with self.assertRaises(EvolutionSandboxError):
            self.supervisor.execute(bad)
        self.popen.assert_not_called()

    def test_rejects_workspace_escape(self):
        bad = SandboxExecutionSpec(
            **{**self.spec.__dict__, "workspace": str(self.root.parent)}
        )
        with self.assertRaises(EvolutionSandboxError):
            self.supervisor.execute(bad)
        self.popen.assert_not_called()

    def test_rejects_bad_policy_before_start(self):
        bad_policy = SandboxPolicy(network_mode="allowlist",
                                   capabilities=frozenset({"write_workspace", "network_allowlist"}))
        bad = SandboxExecutionSpec(**{**self.spec.__dict__, "policy": bad_policy})
        with self.assertRaises(EvolutionSandboxError):
            self.supervisor.execute(bad)
        self.popen.assert_not_called()

    def test_rejects_non_tuple_command(self):
        bad = SandboxExecutionSpec(**{**self.spec.__dict__, "command": ["python"]})
        with self.assertRaises(EvolutionSandboxError):
            self.supervisor.execute(bad)
        self.popen.assert_not_called()

    def test_argv_builder_failure_fails_closed(self):
        supervisor = EvolutionSandboxSupervisor(
            workspace_root=self.root, popen_factory=self.popen,
            argv_builder=Mock(side_effect=RuntimeError("secret")),
        )
        with self.assertRaisesRegex(EvolutionSandboxError, "construction failed"):
            supervisor.execute(self.spec)
        self.popen.assert_not_called()

    def test_output_is_capped_across_both_streams(self):
        self.process.stdout = FakeStream([b"x" * 48])
        self.process.stderr = FakeStream([b"y" * 48])
        self.process.returncode = None
        self.process.poll = lambda: None
        def wait(timeout=None):
            self.process.returncode = 0
            return 0
        self.process.wait = wait
        result = self.supervisor.execute(self.spec)
        self.assertTrue(result.output_limit_exceeded)
        self.assertLessEqual(len(result.stdout.encode()) + len(result.stderr.encode()), 64)

    def test_cancel_signal_is_observed(self):
        event = threading.Event()
        event.set()
        self.process.returncode = None
        self.process.poll = lambda: None
        def wait(timeout=None):
            self.process.returncode = -15
            return -15
        self.process.wait = wait
        result = self.supervisor.execute(self.spec, cancel_event=event)
        self.assertTrue(result.cancelled)

    def test_non_docker_backend_argv_is_not_rewritten(self):
        self.builder.return_value = ["/usr/bin/podman", "run", self.spec.image, "python"]
        self.supervisor.execute(self.spec)
        self.assertEqual(self.popen.call_args.args[0][0], "/usr/bin/podman")


if __name__ == "__main__":
    unittest.main()
