import unittest

from core.linux_host_evidence import LinuxProcessSnapshot
from core.linux_namespace_comparison import compare_linux_namespaces
from core.sandbox_runtime import SandboxRuntimeError


def snapshot(pid, identities, start=100):
    return LinuxProcessSnapshot(
        pid=pid,
        start_time_ticks=start,
        namespace_links=tuple(identities.items()),
        real_uid=0,
        effective_uid=0,
        real_gid=0,
        effective_gid=0,
        no_new_privileges=1,
        seccomp_mode=2,
        effective_capabilities_hex="0",
        cgroup_v2_path="/",
        observed_sources=("test",),
        unknown_controls=(),
        attested=False,
    )


@unittest.skipUnless(os.name == "posix", "Linux diagnostic fixture requires POSIX filesystem semantics")
class LinuxNamespaceComparisonTests(unittest.TestCase):
    def test_reports_shared_and_distinct_namespace_types(self):
        baseline = snapshot(1, {
            "user": "user:[1]", "pid": "pid:[1]", "mnt": "mnt:[1]",
            "ipc": "ipc:[1]", "net": "net:[1]",
        })
        workload = snapshot(20, {
            "user": "user:[2]", "pid": "pid:[2]", "mnt": "mnt:[2]",
            "ipc": "ipc:[1]", "net": "net:[2]",
        })
        report = compare_linux_namespaces(baseline, workload)
        self.assertEqual(report.schema_version, 1)
        self.assertEqual(report.shared_namespace_types, ("ipc",))
        self.assertEqual(report.distinct_namespace_types, ("user", "pid", "mnt", "net"))
        self.assertFalse(report.attested)
        self.assertFalse(report.verified)
        self.assertTrue(report.unknown_controls)

    def test_reports_all_shared_as_shared_not_isolated(self):
        values = {
            "user": "user:[1]", "pid": "pid:[1]", "mnt": "mnt:[1]",
            "ipc": "ipc:[1]", "net": "net:[1]",
        }
        report = compare_linux_namespaces(snapshot(1, values), snapshot(2, values))
        self.assertEqual(report.shared_namespace_types, ("user", "pid", "mnt", "ipc", "net"))
        self.assertEqual(report.distinct_namespace_types, ())

    def test_rejects_incomplete_or_duplicate_namespace_data(self):
        baseline = snapshot(1, {
            "user": "user:[1]", "pid": "pid:[1]", "mnt": "mnt:[1]",
            "ipc": "ipc:[1]", "net": "net:[1]",
        })
        incomplete = snapshot(2, {"user": "user:[2]"})
        with self.assertRaises(SandboxRuntimeError):
            compare_linux_namespaces(baseline, incomplete)
        duplicate = LinuxProcessSnapshot(
            **{**baseline.__dict__, "pid": 2,
               "namespace_links": baseline.namespace_links + (("net", "net:[9]"),)}
        )
        with self.assertRaises(SandboxRuntimeError):
            compare_linux_namespaces(baseline, duplicate)

    def test_rejects_same_process_identity(self):
        values = {
            "user": "user:[1]", "pid": "pid:[1]", "mnt": "mnt:[1]",
            "ipc": "ipc:[1]", "net": "net:[1]",
        }
        with self.assertRaises(SandboxRuntimeError):
            compare_linux_namespaces(snapshot(7, values), snapshot(7, values))


if __name__ == "__main__":
    unittest.main()
