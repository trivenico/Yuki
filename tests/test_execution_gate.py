import unittest
from unittest.mock import patch

from core.execution_gate import run_gate
from core.execution_gate_protocol import ExecutionGateAuthorization, ExecutionGateBinding


class ExecutionGateExecutableTests(unittest.TestCase):
    def setUp(self):
        self.binding = ExecutionGateBinding(
            task_id="task-123",
            worker_id="worker-1",
            container_id="a" * 64,
            spec_id="python-review",
            image="python@sha256:" + "b" * 64,
        )
        self.argv = [
            "--socket", "/run/yuki-gate/authorization.sock",
            "--host-uid", "1000",
            "--task-id", self.binding.task_id,
            "--worker-id", self.binding.worker_id,
            "--container-id", self.binding.container_id,
            "--spec-id", self.binding.spec_id,
            "--image", self.binding.image,
        ]

    def test_execs_only_authenticated_argv_and_wipes_key_buffer(self):
        key = bytearray(b"k" * 32)
        authorization = ExecutionGateAuthorization(
            binding=self.binding, nonce="n" * 64, issued_at=1,
            expires_at=31, command=("/usr/bin/python3", "-I", "task.py"),
        )
        with patch("core.execution_gate._receive", return_value=("signed-token", key)), \
             patch("core.execution_gate.consume_execution_gate_authorization", return_value=authorization) as consume, \
             patch("core.execution_gate.os.execvpe", side_effect=OSError("exec intercepted")) as execute:
            with self.assertRaisesRegex(OSError, "intercepted"):
                run_gate(self.argv)
        self.assertEqual(consume.call_args.kwargs["expected"], self.binding)
        execute.assert_called_once()
        self.assertEqual(execute.call_args.args[:2], (
            "/usr/bin/python3", ["/usr/bin/python3", "-I", "task.py"],
        ))
        self.assertEqual(key, bytearray(32))

    def test_invalid_authorization_never_executes_and_wipes_key(self):
        key = bytearray(b"x" * 32)
        with patch("core.execution_gate._receive", return_value=("bad-token", key)), \
             patch("core.execution_gate.consume_execution_gate_authorization", side_effect=ValueError("invalid")), \
             patch("core.execution_gate.os.execvpe") as execute:
            with self.assertRaisesRegex(ValueError, "invalid"):
                run_gate(self.argv)
        execute.assert_not_called()
        self.assertEqual(key, bytearray(32))

    def test_rejects_invalid_gate_timeout_before_connecting(self):
        argv = self.argv + ["--timeout", "999"]
        with patch("core.execution_gate._receive") as receive:
            with self.assertRaisesRegex(ValueError, "timeout"):
                run_gate(argv)
        receive.assert_not_called()


if __name__ == "__main__":
    unittest.main()
