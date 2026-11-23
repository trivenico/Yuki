import tempfile
import unittest
from pathlib import Path

from core.execution_gate_identity import (
    ExecutionGateIdentityBinder,
    ExecutionGateIdentityError,
    LinuxGateProcessIdentityResolver,
)


class FakeListener:
    def __init__(self):
        self.bound = []

    def bind_gate_identity(self, *, gate_pid, gate_uid):
        self.bound.append((gate_pid, gate_uid))


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
            listener=listener, uid_resolver=lambda pid: 100123 if pid == self.pid else -1,
        )
        container_id = "a" * 64
        inspected = {"Id": container_id, "State": {"Running": True, "Pid": self.pid}}
        binder(container_id, self.pid, inspected)
        self.assertEqual(listener.bound, [(self.pid, 100123)])
        with self.assertRaises(ExecutionGateIdentityError):
            binder(container_id, self.pid, inspected)

    def test_binder_rejects_mismatched_docker_inspection(self):
        listener = FakeListener()
        binder = ExecutionGateIdentityBinder(listener=listener, uid_resolver=lambda _pid: 100123)
        with self.assertRaises(ExecutionGateIdentityError):
            binder("a" * 64, self.pid, {"Id": "b" * 64, "State": {"Running": True, "Pid": self.pid}})
        self.assertEqual(listener.bound, [])


if __name__ == "__main__":
    unittest.main()
