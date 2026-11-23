import tempfile
import unittest
from pathlib import Path

from core.execution_gate_identity import (
    ExecutionGateIdentityBinder,
    ExecutionGateIdentityError,
    LinuxGateProcessIdentityResolver,
    LinuxGateExecTransitionObserver,
)


class FakeListener:
    def __init__(self):
        self.bound = []

    def bind_gate_identity(self, *, gate_pid, gate_uid):
        self.bound.append((gate_pid, gate_uid))


class FakeIdentityResolver:
    def __call__(self, pid):
        return self.resolve_identity(pid)[0]

    def resolve_identity(self, pid):
        return (100123, "987654") if pid == 4321 else (-1, "987654")


class LinuxGateProcessIdentityResolverTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.pid = 4321
        self.pid_dir = self.root / str(self.pid)
        self.pid_dir.mkdir()
        self.argv0 = "/usr/local/bin/yuki-execution-gate"
        self.write_process()

    def tearDown(self):
        self.temp.cleanup()

    def write_process(self, *, argv0=None, uid="100123"):
        self.argv0 = argv0 or self.argv0
        # Linux /proc/<pid>/stat fields 3..22; field 22 is starttime.
        suffix = ["S"] + ["0"] * 18 + ["987654"]
        (self.pid_dir / "stat").write_text(
            f"{self.pid} (yuki gate) " + " ".join(suffix), encoding="ascii"
        )
        (self.pid_dir / "cmdline").write_bytes(self.argv0.encode() + b"\x00--socket\x00/run/yuki-gate.sock\x00")
        (self.pid_dir / "status").write_text(
            f"Name:\tyuki-gate\nUid:\t{uid}\t{uid}\t{uid}\t{uid}\n",
            encoding="ascii",
        )

    def test_resolves_host_visible_effective_uid_for_exact_gate_argv(self):
        resolver = LinuxGateProcessIdentityResolver(proc_root=str(self.root))
        self.assertEqual(resolver(self.pid), 100123)

    def test_rejects_other_process_even_with_valid_uid(self):
        self.write_process(argv0="/usr/bin/python")
        resolver = LinuxGateProcessIdentityResolver(proc_root=str(self.root))
        with self.assertRaises(ExecutionGateIdentityError):
            resolver(self.pid)

    def test_rejects_inconsistent_uid_credentials(self):
        (self.pid_dir / "status").write_text(
            "Name:\tyuki-gate\nUid:\t100123\t100124\t100123\t100123\n",
            encoding="ascii",
        )
        resolver = LinuxGateProcessIdentityResolver(proc_root=str(self.root))
        with self.assertRaises(ExecutionGateIdentityError):
            resolver(self.pid)

    def test_rejects_invalid_pid(self):
        resolver = LinuxGateProcessIdentityResolver(proc_root=str(self.root))
        with self.assertRaises(ValueError):
            resolver(0)

    def test_binder_checks_daemon_identity_and_binds_exact_pid_uid(self):
        listener = FakeListener()
        binder = ExecutionGateIdentityBinder(
            listener=listener, uid_resolver=FakeIdentityResolver(),
        )
        container_id = "a" * 64
        inspected = {"Id": container_id, "State": {"Running": True, "Pid": self.pid}}
        binder(container_id, self.pid, inspected)
        self.assertEqual(listener.bound, [(self.pid, 100123)])
        self.assertEqual(binder.start_time, "987654")
        with self.assertRaises(ExecutionGateIdentityError):
            binder(container_id, self.pid, inspected)

    def test_binder_rejects_uid_only_resolver_without_process_start_time(self):
        listener = FakeListener()
        with self.assertRaisesRegex(TypeError, "resolve_identity"):
            ExecutionGateIdentityBinder(
                listener=listener, uid_resolver=lambda _pid: 100123,
            )
        self.assertEqual(listener.bound, [])

    def test_binder_rejects_mismatched_docker_inspection(self):
        listener = FakeListener()
        binder = ExecutionGateIdentityBinder(listener=listener, uid_resolver=FakeIdentityResolver())
        with self.assertRaises(ExecutionGateIdentityError):
            binder("a" * 64, self.pid, {"Id": "b" * 64, "State": {"Running": True, "Pid": self.pid}})
        self.assertEqual(listener.bound, [])


if __name__ == "__main__":
    unittest.main()


class LinuxGateExecTransitionObserverTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.pid = 7654
        self.pid_dir = self.root / str(self.pid)
        self.pid_dir.mkdir()
        self.write_process(["/usr/local/bin/python", "-m", "task"], uid=100123)

    def tearDown(self):
        self.temp.cleanup()

    def write_process(self, argv, *, uid):
        suffix = ["S"] + ["0"] * 18 + ["123456"]
        (self.pid_dir / "stat").write_text(
            f"{self.pid} (yuki gate) " + " ".join(suffix), encoding="ascii"
        )
        (self.pid_dir / "cmdline").write_bytes(
            b"\x00".join(item.encode() for item in argv) + b"\x00"
        )
        (self.pid_dir / "status").write_text(
            f"Name:\\ttask\\nUid:\\t{uid}\\t{uid}\\t{uid}\\t{uid}\\n",
            encoding="ascii",
        )

    def test_exec_observer_rejects_pid_replacement_since_gate_binding(self):
        observer = LinuxGateExecTransitionObserver(proc_root=str(self.root))
        with self.assertRaisesRegex(ExecutionGateIdentityError, "replaced"):
            observer.wait_for_exec(
                gate_pid=self.pid, expected_uid=100123,
                expected_argv=["/usr/local/bin/python", "-m", "task"],
                expected_start_time="999999", timeout_seconds=0.1,
            )

    def test_exec_observer_uses_real_procfs_nul_delimited_argv(self):
        observer = LinuxGateExecTransitionObserver(proc_root=str(self.root))
        self.assertTrue(observer.wait_for_exec(
            gate_pid=self.pid, expected_uid=100123,
            expected_argv=["/usr/local/bin/python", "-m", "task"],
            expected_start_time="123456", timeout_seconds=0.1,
        ))

    def test_confirms_exact_authorized_exec_argv(self):
        observer = LinuxGateExecTransitionObserver(proc_root=str(self.root))
        self.assertTrue(observer.wait_for_exec(
            gate_pid=self.pid, expected_uid=100123,
            expected_argv=["/usr/local/bin/python", "-m", "task"],
            timeout_seconds=0.1,
        ))

    def test_does_not_confirm_different_command(self):
        observer = LinuxGateExecTransitionObserver(proc_root=str(self.root))
        self.assertFalse(observer.wait_for_exec(
            gate_pid=self.pid, expected_uid=100123,
            expected_argv=["/bin/sh", "-c", "untrusted"],
            timeout_seconds=0.05,
        ))

    def test_rejects_uid_change(self):
        self.write_process(["/usr/local/bin/python", "-m", "task"], uid=100124)
        observer = LinuxGateExecTransitionObserver(proc_root=str(self.root))
        with self.assertRaises(ExecutionGateIdentityError):
            observer.wait_for_exec(
                gate_pid=self.pid, expected_uid=100123,
                expected_argv=["/usr/local/bin/python", "-m", "task"],
                timeout_seconds=0.1,
            )

    def test_rejects_unbounded_or_invalid_inputs(self):
        observer = LinuxGateExecTransitionObserver(proc_root=str(self.root))
        with self.assertRaises(ValueError):
            observer.wait_for_exec(
                gate_pid=self.pid, expected_uid=100123, expected_argv=[],
            )
        with self.assertRaises(ValueError):
            observer.wait_for_exec(
                gate_pid=self.pid, expected_uid=100123,
                expected_argv=["/bin/true"], timeout_seconds=20,
            )
