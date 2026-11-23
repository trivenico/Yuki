import unittest

from core.execution_gate_protocol import (
    ExecutionGateAuthorizationError,
    ExecutionGateBinding,
    ExecutionGateReplayGuard,
    consume_execution_gate_authorization,
    issue_execution_gate_authorization,
)


class ExecutionGateProtocolTests(unittest.TestCase):
    def setUp(self):
        self.key = b"k" * 32
        self.binding = ExecutionGateBinding(
            task_id="task-123", worker_id="worker-1",
            container_id="a" * 64, spec_id="python-review",
            image="python@sha256:" + "b" * 64,
        )

    def issue(self, **kwargs):
        return issue_execution_gate_authorization(
            key=self.key, binding=self.binding,
            command=("/usr/local/bin/yuki-task", "--safe"),
            now=1_700_000_000, **kwargs,
        )

    def consume(self, token, *, expected=None, guard=None, now=1_700_000_010):
        return consume_execution_gate_authorization(
            token=token, key=self.key, expected=expected or self.binding,
            replay_guard=guard or ExecutionGateReplayGuard(), now=now,
        )

    def test_round_trip_preserves_exact_argv_and_execution_binding(self):
        token = self.issue()
        result = self.consume(token)
        self.assertEqual(result.binding, self.binding)
        self.assertEqual(result.command, ("/usr/local/bin/yuki-task", "--safe"))
        self.assertEqual(result.expires_at, 1_700_000_030)

    def test_nonce_is_fresh_for_each_issued_authorization(self):
        first = self.issue()
        second = self.issue()
        self.assertNotEqual(first, second)

    def test_rejects_replay_within_gate_process(self):
        token = self.issue()
        guard = ExecutionGateReplayGuard()
        self.consume(token, guard=guard)
        with self.assertRaisesRegex(ExecutionGateAuthorizationError, "already been consumed"):
            self.consume(token, guard=guard)

    def test_rejects_wrong_task_worker_container_spec_or_image(self):
        token = self.issue()
        variants = (
            ExecutionGateBinding("other-task", "worker-1", "a" * 64, "python-review", self.binding.image),
            ExecutionGateBinding("task-123", "other-worker", "a" * 64, "python-review", self.binding.image),
            ExecutionGateBinding("task-123", "worker-1", "c" * 64, "python-review", self.binding.image),
            ExecutionGateBinding("task-123", "worker-1", "a" * 64, "other-spec", self.binding.image),
            ExecutionGateBinding("task-123", "worker-1", "a" * 64, "python-review", "other@sha256:" + "d" * 64),
        )
        for binding in variants:
            with self.subTest(binding=binding):
                with self.assertRaises(ExecutionGateAuthorizationError):
                    self.consume(token, expected=binding)

    def test_rejects_tampered_or_truncated_token(self):
        token = self.issue()
        replacement = "A" if token[-3] != "A" else "B"
        with self.assertRaises(ExecutionGateAuthorizationError):
            self.consume(token[:-3] + replacement + token[-2:])
        with self.assertRaises(ExecutionGateAuthorizationError):
            self.consume(token[:-8])

    def test_rejects_expired_and_future_issued_tokens(self):
        token = self.issue()
        with self.assertRaisesRegex(ExecutionGateAuthorizationError, "stale"):
            self.consume(token, now=1_700_000_031)
        future = self.issue(now=1_700_000_100)
        with self.assertRaisesRegex(ExecutionGateAuthorizationError, "stale"):
            self.consume(future, now=1_700_000_010)

    def test_rejects_shell_string_and_unbounded_command(self):
        for command in ("python -c 'bad'", (), ("",), ("x" * 4097,)):
            with self.subTest(command=command):
                with self.assertRaises(ValueError):
                    issue_execution_gate_authorization(
                        key=self.key, binding=self.binding, command=command,
                        now=1_700_000_000,
                    )

    def test_rejects_short_key_and_invalid_binding(self):
        with self.assertRaises(ValueError):
            issue_execution_gate_authorization(
                key=b"short", binding=self.binding, command=("true",),
            )
        with self.assertRaises(ValueError):
            ExecutionGateBinding(
                task_id="task", worker_id="worker", container_id="not-a-container",
                spec_id="spec", image=self.binding.image,
            )

    def test_replay_registry_fails_closed_when_full(self):
        guard = ExecutionGateReplayGuard(maximum_entries=1)
        token = self.issue()
        self.consume(token, guard=guard)
        second = self.issue()
        with self.assertRaisesRegex(ExecutionGateAuthorizationError, "registry is full"):
            self.consume(second, guard=guard)


if __name__ == "__main__":
    unittest.main()
