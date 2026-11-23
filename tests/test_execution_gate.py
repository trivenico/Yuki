import socket
import unittest
from unittest.mock import patch

from core.execution_gate import run_gate
from core.execution_gate_protocol import (
    ExecutionGateAuthorization, ExecutionGateBinding, ExecutionGateExpectedIdentity,
)


class ExecutionGateExecutableTests(unittest.TestCase):
    def setUp(self):
        self.binding = ExecutionGateBinding(
            container_id="a" * 64, task_id="task-123", worker_id="worker-1",
            spec_id="python-review", image="python@sha256:" + "b" * 64,
        )
        self.argv = [
            "--socket", "/run/yuki-gate/authorization.sock", "--host-uid", "1000",
            "--task-id", self.binding.task_id, "--worker-id", self.binding.worker_id,
            "--spec-id", self.binding.spec_id, "--image", self.binding.image,
        ]

    def test_acknowledges_only_after_valid_authorization_then_execs_pinned_argv(self):
        key = bytearray(b"k" * 32)
        authorization = ExecutionGateAuthorization(
            binding=self.binding, nonce="n" * 64, issued_at=1, expires_at=31,
            command=("/usr/bin/python3", "-I", "task.py"),
        )
        left, right = socket.socketpair()
        self.addCleanup(left.close)
        self.addCleanup(right.close)
        with patch("core.execution_gate._receive", return_value=("signed-token", key, left)), \
             patch("core.execution_gate.consume_execution_gate_authorization", return_value=authorization) as consume, \
             patch("core.execution_gate.acknowledge_gate_authorization") as acknowledge, \
             patch("core.execution_gate.os.execvpe", side_effect=OSError("exec intercepted")) as execute:
            with self.assertRaisesRegex(OSError, "intercepted"):
                run_gate(self.argv)
        consume.assert_called_once()
        acknowledge.assert_called_once_with(left)
        execute.assert_called_once_with(
            "/usr/bin/python3", ["/usr/bin/python3", "-I", "task.py"], unittest.mock.ANY,
        )
        self.assertEqual(key, bytearray(32))

    def test_invalid_authorization_never_acknowledges_or_executes_and_wipes_key(self):
        key = bytearray(b"x" * 32)
        left, right = socket.socketpair()
        self.addCleanup(left.close)
        self.addCleanup(right.close)
        with patch("core.execution_gate._receive", return_value=("bad-token", key, left)), \
             patch("core.execution_gate.consume_execution_gate_authorization", side_effect=ValueError("invalid")), \
             patch("core.execution_gate.acknowledge_gate_authorization") as acknowledge, \
             patch("core.execution_gate.os.execvpe") as execute:
            with self.assertRaisesRegex(ValueError, "invalid"):
                run_gate(self.argv)
        acknowledge.assert_not_called()
        execute.assert_not_called()
        self.assertEqual(key, bytearray(32))

    def test_rejects_nonstandard_socket_and_root_host_identity(self):
        with patch("core.execution_gate._receive") as receive:
            bad_path = list(self.argv)
            bad_path[1] = "/workspace/authorization.sock"
            with self.assertRaisesRegex(ValueError, "private runtime"):
                run_gate(bad_path)
            bad_uid = list(self.argv)
            bad_uid[3] = "0"
            with self.assertRaisesRegex(ValueError, "non-root"):
                run_gate(bad_uid)
        receive.assert_not_called()


if __name__ == "__main__":
    unittest.main()
