import tempfile
import unittest
from pathlib import Path

from core.execution_gate_session import (
    ExecutionGateAuthorizationSession,
    ExecutionGateSessionError,
)


IMAGE = "registry.example/yuki@sha256:" + "a" * 64


class FakeListener:
    def __init__(self, *, socket_path, timeout_seconds):
        self.socket_path = socket_path
        self.timeout_seconds = timeout_seconds
        self.expected_gate_uid = None
        self.opened = False
        self.deliveries = []
        self.closed = False

    def open(self):
        self.opened = True

    def bind_gate_identity(self, *, gate_pid, gate_uid):
        if self.expected_gate_uid is not None:
            raise RuntimeError("already bound")
        self.expected_gate_uid = gate_uid

    def deliver(self, *, token, key):
        if not self.opened or self.expected_gate_uid is None:
            raise RuntimeError("not ready")
        self.deliveries.append((token, key))

    def close(self):
        self.closed = True


class FakeIdentityResolver:
    def __call__(self, pid):
        return self.resolve_identity(pid)[0]

    def resolve_identity(self, pid):
        return 1000, "123456"


class ExecutionGateAuthorizationSessionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.directory.chmod(0o700)
        self.listener = None
        self.observations = []

    def tearDown(self):
        self.temp.cleanup()

    def build(self, **overrides):
        def listener_factory(**kwargs):
            self.listener = FakeListener(**kwargs)
            return self.listener

        options = dict(
            private_gate_directory=str(self.directory),
            task_id="task-1",
            worker_id="worker-1",
            spec_id="profile-1",
            image=IMAGE,
            expected_host_uid=1000,
            uid_resolver=FakeIdentityResolver(),
            exec_observer=lambda pid, **kwargs: (
                self.observations.append((pid, kwargs)) or True
            ),
            key_factory=lambda size: b"k" * size,
            listener_factory=listener_factory,
        )
        options.update(overrides)
        return ExecutionGateAuthorizationSession(**options)

    def inspected(self, pid=4321):
        return {"Id": "d" * 64, "State": {"Running": True, "Pid": pid}}

    def test_binds_identity_then_delivers_and_confirms_exact_exec(self):
        session = self.build()
        self.assertEqual(
            session.gate_arguments,
            (
                "--socket", "/run/yuki-gate/authorization.sock",
                "--host-uid", "1000",
                "--task-id", "task-1",
                "--worker-id", "worker-1",
                "--spec-id", "profile-1",
                "--image", IMAGE,
                "--timeout", "30.0",
            ),
        )
        session.open()
        session.bind_gate_identity("d" * 64, 4321, self.inspected())
        session.release(
            "task-1", "worker-1", "d" * 64, "profile-1", IMAGE,
            ("/usr/bin/python3", "-m", "worker"), object(),
        )
        self.assertEqual(len(self.listener.deliveries), 1)
        token, key = self.listener.deliveries[0]
        self.assertTrue(token)
        self.assertEqual(key, b"k" * 32)
        self.assertEqual(self.observations[0][0], 4321)
        self.assertEqual(self.observations[0][1]["expected_uid"], 1000)
        self.assertEqual(
            self.observations[0][1]["expected_argv"],
            ("/usr/bin/python3", "-m", "worker"),
        )
        self.assertEqual(self.observations[0][1]["expected_start_time"], "123456")
        with self.assertRaises(ExecutionGateSessionError):
            session.release(
                "task-1", "worker-1", "d" * 64, "profile-1", IMAGE,
                ("/bin/true",), object(),
            )
        session.close()
        self.assertTrue(self.listener.closed)

    def test_release_before_identity_binding_fails_closed(self):
        session = self.build()
        session.open()
        with self.assertRaisesRegex(ExecutionGateSessionError, "identity"):
            session.release(
                "task-1", "worker-1", "d" * 64, "profile-1", IMAGE,
                ("/bin/true",), object(),
            )
        self.assertEqual(self.listener.deliveries, [])
        session.close()

    def test_execution_binding_mismatch_fails_before_delivery(self):
        session = self.build()
        session.open()
        session.bind_gate_identity("d" * 64, 4321, self.inspected())
        with self.assertRaisesRegex(ExecutionGateSessionError, "prepared execution"):
            session.release(
                "another-task", "worker-1", "d" * 64, "profile-1", IMAGE,
                ("/bin/true",), object(),
            )
        self.assertEqual(self.listener.deliveries, [])
        session.close()

    def test_unobserved_exec_is_not_reported_as_started(self):
        session = self.build(exec_observer=lambda *_args, **_kwargs: False)
        session.open()
        session.bind_gate_identity("d" * 64, 4321, self.inspected())
        with self.assertRaisesRegex(ExecutionGateSessionError, "not observed"):
            session.release(
                "task-1", "worker-1", "d" * 64, "profile-1", IMAGE,
                ("/bin/true",), object(),
            )
        self.assertEqual(len(self.listener.deliveries), 1)
        session.close()


if __name__ == "__main__":
    unittest.main()
