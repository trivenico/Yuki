import os
import tempfile
import unittest
from pathlib import Path

from core.linux_sandbox_evidence import collect_linux_sandbox_evidence
from core.sandbox_runtime import SandboxRuntimeError


def make_fixture(root: Path):
    proc = root / "proc"
    cgroup = root / "cgroup"
    cgroup.mkdir()
    process = proc / "123"
    ns = process / "ns"
    ns.mkdir(parents=True)
    (process / "stat").write_text("123 (worker ) name) S " + " ".join(["0"] * 18) + " 987654\n", encoding="ascii")
    (process / "status").write_text(
        "Uid:\t1000\t1000\t1000\t1000\n"
        "Gid:\t1000\t1000\t1000\t1000\n"
        "NoNewPrivs:\t1\nSeccomp:\t2\nCapEff:\t0000000000000000\n",
        encoding="ascii",
    )
    for name in ("user", "pid", "mnt", "ipc", "net"):
        (ns / name).symlink_to("user:[4026531837]")
    (process / "cgroup").write_text("0::/yuki/job-123\n", encoding="ascii")
    (cgroup / "cgroup.controllers").write_text("cpu memory pids\n", encoding="ascii")
    leaf = cgroup / "yuki" / "job-123"
    leaf.mkdir(parents=True)
    (leaf / "cpu.max").write_text("20000 100000\n", encoding="ascii")
    (leaf / "memory.max").write_text("268435456\n", encoding="ascii")
    (leaf / "memory.swap.max").write_text("0\n", encoding="ascii")
    (leaf / "pids.max").write_text("32\n", encoding="ascii")
    return proc, cgroup


@unittest.skipUnless(os.name == "posix", "Linux diagnostic fixture requires POSIX filesystem semantics")
class LinuxSandboxEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.proc, self.cgroup = make_fixture(self.root)

    def collect(self):
        return collect_linux_sandbox_evidence(
            123, proc_root=str(self.proc), cgroup_root=str(self.cgroup)
        )

    def test_combines_process_and_cgroup_with_versioned_non_attesting_envelope(self):
        report = self.collect()
        self.assertEqual(report.schema_version, 1)
        self.assertEqual(report.evidence_class, "diagnostic_only")
        self.assertEqual(report.process.pid, 123)
        self.assertEqual(report.cgroup.cgroup_path, "/yuki/job-123")
        self.assertEqual(report.cgroup.memory_max_bytes, 268435456)
        self.assertFalse(report.attested)
        self.assertFalse(report.verified)
        self.assertGreaterEqual(report.collection_duration_ns, 0)
        self.assertLessEqual(report.observation_started_unix_ns, report.observation_finished_unix_ns)
        self.assertTrue(any("not an atomic snapshot" in item for item in report.unknown_controls))

    def test_rejects_process_without_cgroup_v2_membership(self):
        (self.proc / "123" / "cgroup").write_text("5:cpu:/legacy\n", encoding="ascii")
        with self.assertRaises(SandboxRuntimeError):
            self.collect()

    def test_rejects_missing_required_cgroup_controls(self):
        (self.cgroup / "yuki" / "job-123" / "pids.max").unlink()
        with self.assertRaises(SandboxRuntimeError):
            self.collect()

    def test_rejects_missing_process_identity(self):
        with self.assertRaises(SandboxRuntimeError):
            collect_linux_sandbox_evidence(
                999, proc_root=str(self.proc), cgroup_root=str(self.cgroup)
            )


class LinuxSandboxEvidenceBuilderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.proc = self.root / "proc"
        self.cgroup = self.root / "cgroup"
        self.approved = self.root / "approved"
        self.workspace = self.approved / "task-1"
        self.workspace.mkdir(parents=True)
        process = self.proc / "456"
        process.mkdir(parents=True)
        (process / "cgroup").write_text("0::/yuki/task-1\\n", encoding="ascii")
        leaf = self.cgroup / "yuki" / "task-1"
        leaf.mkdir(parents=True)
        (leaf / "cpu.max").write_text("25000 100000\\n", encoding="ascii")
        (leaf / "memory.max").write_text(str(128 * 1024 * 1024) + "\\n", encoding="ascii")
        (leaf / "pids.max").write_text("32\\n", encoding="ascii")
        from automation.agent_execution_spec import AgentExecutionSpec, ExecutionBudget
        from core.sandbox_executor import SandboxExecutionProfile, DockerSandboxExecutor
        from core.sandbox_policy import SandboxPolicy
        self.spec = AgentExecutionSpec(
            spec_id="docker-inspect", role="reviewer", instructions="Inspect safely.",
            capabilities=("coding",), budget=ExecutionBudget(
                max_runtime_seconds=30, max_memory_mb=128, max_tokens=200, max_steps=2,
            ),
        )
        self.profile = SandboxExecutionProfile(
            spec_id=self.spec.spec_id, image="python@sha256:" + "a" * 64,
            policy=SandboxPolicy(timeout_seconds=30, memory_mb=128, cpu_millis=250, max_processes=32),
            command=("python", "--version"), workspace_resolver=lambda _: str(self.workspace),
        )
        self.container = {
            "State": {"Running": True, "Pid": 456},
            "Mounts": [{
                "Type": "bind", "Source": str(self.workspace),
                "Destination": "/workspace", "RW": False,
            }],
        }
        self.probe_calls = []
        self.probe = lambda cid, image: (
            self.probe_calls.append((cid, image)) or {
                "runtime_versions": {"python": "3.12.2"},
                "installed_dependencies": {"pip": "24.0"},
            }
        )

    @unittest.skipUnless(os.name == "posix" and hasattr(os, "O_TMPFILE"), "Linux host evidence requires O_TMPFILE")
    def test_builds_evidence_from_cgroup_workspace_and_fixed_runtime_probe(self):
        from core.linux_sandbox_evidence import LinuxSandboxEvidenceBuilder
        from core.sandbox_executor import DockerSandboxExecutor
        builder = LinuxSandboxEvidenceBuilder(
            approved_workspace_root=str(self.approved), runtime_probe=self.probe,
            verifier_id="linux-host-agent", proc_root=str(self.proc),
            cgroup_root=str(self.cgroup),
        )
        evidence = builder(
            "task-1", "worker-1", "d" * 64, self.container, self.spec, self.profile,
        )
        self.assertEqual(evidence.cpu_millis, 250)
        self.assertEqual(evidence.memory_mb, 128)
        self.assertEqual(evidence.worker_id, "worker-1")
        self.assertEqual(evidence.runtime_versions["python"], "3.12.2")
        self.assertEqual(evidence.approved_workspace_root_id, evidence.workspace_root_id)
        self.assertTrue(evidence.workspace_within_approved_root)
        self.assertTrue(evidence.workspace_symlink_free)
        self.assertTrue(evidence.workspace_writable)
        self.assertEqual(len(self.probe_calls), 1)
        self.assertEqual(evidence.sandbox_policy_sha256, DockerSandboxExecutor._policy_digest(self.profile.policy))

    @unittest.skipUnless(os.name == "posix" and hasattr(os, "O_TMPFILE"), "Linux host evidence requires O_TMPFILE")
    def test_rejects_cgroup_limit_mismatch_and_workspace_escape_before_probe(self):
        from core.linux_sandbox_evidence import LinuxSandboxEvidenceBuilder
        from core.host_runtime_inventory import RuntimeInventoryError
        builder = LinuxSandboxEvidenceBuilder(
            approved_workspace_root=str(self.approved), runtime_probe=self.probe,
            verifier_id="linux-host-agent", proc_root=str(self.proc),
            cgroup_root=str(self.cgroup),
        )
        (self.cgroup / "yuki" / "task-1" / "memory.max").write_text(str(256 * 1024 * 1024), encoding="ascii")
        with self.assertRaises(RuntimeInventoryError):
            builder("task-1", "worker-1", "d" * 64, self.container, self.spec, self.profile)
        self.assertEqual(self.probe_calls, [])
        (self.cgroup / "yuki" / "task-1" / "memory.max").write_text(str(128 * 1024 * 1024), encoding="ascii")
        outside = self.root / "outside"
        outside.mkdir()
        self.container["Mounts"][0]["Source"] = str(outside)
        with self.assertRaises(RuntimeInventoryError):
            builder("task-1", "worker-1", "d" * 64, self.container, self.spec, self.profile)
        self.assertEqual(self.probe_calls, [])


if __name__ == "__main__":
    unittest.main()
