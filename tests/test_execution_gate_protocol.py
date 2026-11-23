import base64
import hashlib
import hmac
import unittest

from core.execution_gate_protocol import (
    ExecutionGateAuthorizationError,
    ExecutionGateBinding,
    ExecutionGateExpectedIdentity,
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
        issued_at = kwargs.pop("now", 1_700_000_000)
        return issue_execution_gate_authorization(
            key=self.key, binding=self.binding,
            command=("/usr/local/bin/yuki-task", "--safe"),
            now=issued_at, **kwargs,
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

    def test_gate_can_validate_daemon_assigned_container_id_from_signed_host_envelope(self):
        token = self.issue()
        expected = ExecutionGateExpectedIdentity(
            task_id=self.binding.task_id,
            worker_id=self.binding.worker_id,
            spec_id=self.binding.spec_id,
            image=self.binding.image,
        )
        result = consume_execution_gate_authorization(
            token=token, key=self.key, expected=expected,
            replay_guard=ExecutionGateReplayGuard(), now=1_700_000_010,
        )
        self.assertEqual(result.binding.container_id, self.binding.container_id)

    def test_partial_gate_identity_still_rejects_wrong_task_worker_spec_or_image(self):
        token = self.issue()
        for expected in (
            ExecutionGateExpectedIdentity("other-task", "worker-1", "python-review", self.binding.image),
            ExecutionGateExpectedIdentity("task-123", "other-worker", "python-review", self.binding.image),
            ExecutionGateExpectedIdentity("task-123", "worker-1", "other-spec", self.binding.image),
            ExecutionGateExpectedIdentity("task-123", "worker-1", "python-review", "other@sha256:" + "d" * 64),
        ):
            with self.subTest(expected=expected):
                with self.assertRaises(ExecutionGateAuthorizationError):
                    consume_execution_gate_authorization(
                        token=token, key=self.key, expected=expected,
                        replay_guard=ExecutionGateReplayGuard(), now=1_700_000_010,
                    )

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

    def test_rejects_noncanonical_token_framing_and_duplicate_json_keys(self):
        token = self.issue()
        with self.assertRaises(ExecutionGateAuthorizationError):
            self.consume(token + ".extra")
        with self.assertRaises(ExecutionGateAuthorizationError):
            self.consume("!" + token[1:])

        payload = (
            b'{"protocol":"yuki.execution-gate.v1",'
            b'"protocol":"yuki.execution-gate.v1",'
            b'"task_id":"task-123","worker_id":"worker-1",'
            b'"container_id":"' + b"a" * 64 + b'",'
            b'"spec_id":"python-review","image":"python@sha256:' + b"b" * 64 + b'",'
            b'"nonce":"' + b"c" * 64 + b'",'
            b'"issued_at":1700000000,"expires_at":1700000030,'
            b'"command":["/usr/local/bin/yuki-task","--safe"]}'
        )
        signature = hmac.new(self.key, payload, hashlib.sha256).digest()
        encoded = base64.urlsafe_b64encode(payload).rstrip(b"=")
        signed = encoded + b"." + base64.urlsafe_b64encode(signature).rstrip(b"=")
        with self.assertRaisesRegex(ExecutionGateAuthorizationError, "malformed"):
            self.consume(signed.decode("ascii"))

    def test_rejects_expired_and_future_issued_tokens(self):
        token = self.issue()
        with self.assertRaisesRegex(ExecutionGateAuthorizationError, "stale"):
            self.consume(token, now=1_700_000_031)
        future = self.issue(now=1_700_000_100)
        with self.assertRaisesRegex(ExecutionGateAuthorizationError, "stale"):
            self.consume(future, now=1_700_000_010)

    def test_rejects_shell_string_and_unbounded_command(self):
        for command in ("python -c 'bad'", (), ("",), ("x" * 4097,), ("bad\x00arg",)):
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
