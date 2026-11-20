import tempfile
import unittest
from pathlib import Path

from core.linux_network_evidence import collect_linux_network_snapshot
from core.sandbox_runtime import SandboxRuntimeError


ROUTE_HEADER = "Iface Destination Gateway Flags RefCnt Use Metric Mask MTU Window IRTT\n"
ROUTE_DEFAULT = "eth0 00000000 0101A8C0 0003 0 0 100 00000000 0 0 0\n"
TCP_HEADER = "  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode\n"


def make_proc(root: Path, pid: int = 90):
    process = root / str(pid)
    (process / "net").mkdir(parents=True)
    suffix = "S " + " ".join(["0"] * 18 + ["654321"])
    (process / "stat").write_text(f"{pid} (network worker) {suffix}\n", encoding="ascii")
    (process / "net" / "dev").write_text(
        "Inter-| Receive                                                |  Transmit\n"
        " face |bytes    packets errs drop fifo frame compressed multicast|bytes    packets errs drop fifo colls carrier compressed\n"
        "    lo: 100 1 0 0 0 0 0 0 200 2 0 0 0 0 0 0\n"
        "  eth0: 300 3 0 0 0 0 0 0 400 4 0 0 0 0 0 0\n",
        encoding="ascii",
    )
    (process / "net" / "route").write_text(ROUTE_HEADER + ROUTE_DEFAULT, encoding="ascii")
    ipv6_default = "0" * 32 + " 00 " + "0" * 32 + " 00 " + "0" * 32 + " 00000000 00000000 00000000 00000001 eth0\\n"
    (process / "net" / "ipv6_route").write_text(ipv6_default, encoding="ascii")
    (process / "net" / "tcp").write_text(
        TCP_HEADER
        + "   0: 00000000:1F90 00000000:0000 0A 00000000:00000000 00:00000000 00000000 0 0 12345\n"
        + "   1: 0100007F:1F91 00000000:0000 01 00000000:00000000 00:00000000 00000000 0 0 12346\n",
        encoding="ascii",
    )
    return process


class LinuxNetworkEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.process = make_proc(self.root)

    def collect(self):
        return collect_linux_network_snapshot(90, proc_root=str(self.root))

    def test_collects_interfaces_routes_and_listeners_without_attesting(self):
        report = self.collect()
        self.assertEqual([item.name for item in report.interfaces], ["lo", "eth0"])
        self.assertEqual(report.interfaces[1].rx_bytes, 300)
        self.assertEqual(report.interfaces[1].tx_bytes, 400)
        self.assertTrue(report.has_up_default_route)
        self.assertEqual(len(report.ipv6_routes), 1)
        self.assertTrue(report.ipv6_routes[0].is_default)
        self.assertEqual(report.ipv6_routes[0].interface, "eth0")
        self.assertTrue(report.has_up_ipv6_default_route)
        self.assertEqual(report.tcp_listeners[0].local_address, "0.0.0.0")
        self.assertEqual(report.tcp_listeners[0].local_port, 8080)
        self.assertFalse(report.attested)
        self.assertFalse(report.verified)

    def test_no_default_route_is_not_claimed_as_isolation(self):
        (self.process / "net" / "route").write_text(
            ROUTE_HEADER + "eth0 0001A8C0 00000000 0001 0 0 10 FFFFFFFF 0 0 0\n",
            encoding="ascii",
        )
        report = self.collect()
        self.assertFalse(report.has_up_default_route)
        self.assertTrue(report.unknown_controls)

    def test_ipv6_non_default_route_does_not_count_as_default(self):
        route = "20010db8000000000000000000000000 40 " + "0" * 32 + " 00 " + "0" * 32 + " 0000000a 00000000 00000000 00000001 eth0\\n"
        (self.process / "net" / "ipv6_route").write_text(route, encoding="ascii")
        report = self.collect()
        self.assertFalse(report.has_up_ipv6_default_route)
        self.assertEqual(report.ipv6_routes[0].destination_prefix, 64)

    def test_ipv6_missing_table_is_reported_not_treated_as_isolation(self):
        (self.process / "net" / "ipv6_route").unlink()
        report = self.collect()
        self.assertEqual(report.ipv6_routes, ())
        self.assertFalse(report.has_up_ipv6_default_route)
        self.assertIn("missing IPv6 route table is recorded as unavailable and is not proof IPv6 is disabled", report.unknown_controls)

    def test_rejects_invalid_ipv6_prefix_and_fields(self):
        malformed = "0" * 32 + " 81 " + "0" * 32 + " 00 " + "0" * 32 + " 00000000 00000000 00000000 00000001 eth0\\n"
        (self.process / "net" / "ipv6_route").write_text(malformed, encoding="ascii")
        with self.assertRaises(SandboxRuntimeError):
            self.collect()
        (self.process / "net" / "ipv6_route").write_text("0 00", encoding="ascii")
        with self.assertRaises(SandboxRuntimeError):
            self.collect()

    def test_rejects_symlinked_ipv6_route_table(self):
        target = self.root / "ipv6-target"
        target.write_text((self.process / "net" / "ipv6_route").read_text(encoding="ascii"), encoding="ascii")
        (self.process / "net" / "ipv6_route").unlink()
        (self.process / "net" / "ipv6_route").symlink_to(target)
        with self.assertRaises(SandboxRuntimeError):
            self.collect()

    def test_rejects_malformed_interface_counters(self):
        (self.process / "net" / "dev").write_text("header\nheader\neth0: 1 2 nope\n", encoding="ascii")
        with self.assertRaises(SandboxRuntimeError):
            self.collect()

    def test_supports_legacy_aliased_interface_names(self):
        line = "eth0:1: 1 2 0 0 0 0 0 0 3 4 0 0 0 0 0 0\n"
        (self.process / "net" / "dev").write_text("header\nheader\n" + line, encoding="ascii")
        report = self.collect()
        self.assertEqual(report.interfaces[0].name, "eth0:1")

    def test_rejects_duplicate_interface_names(self):
        line = "eth0: 1 2 0 0 0 0 0 0 1 2 0 0 0 0 0 0\n"
        (self.process / "net" / "dev").write_text("header\nheader\n" + line + line, encoding="ascii")
        with self.assertRaises(SandboxRuntimeError):
            self.collect()

    def test_rejects_malformed_route_mask_and_columns(self):
        (self.process / "net" / "route").write_text(
            ROUTE_HEADER + "eth0 00000000 00000000 0001 0 0 0 nope 0 0 0\n",
            encoding="ascii",
        )
        with self.assertRaises(SandboxRuntimeError):
            self.collect()

    def test_rejects_malformed_tcp_local_endpoint(self):
        (self.process / "net" / "tcp").write_text(TCP_HEADER + "0: bad 00000000:0000 0A 0 0 0 0 0 0\n", encoding="ascii")
        with self.assertRaises(SandboxRuntimeError):
            self.collect()

    def test_rejects_symlinked_network_table(self):
        target = self.root / "dev-target"
        target.write_text((self.process / "net" / "dev").read_text(encoding="ascii"), encoding="ascii")
        (self.process / "net" / "dev").unlink()
        (self.process / "net" / "dev").symlink_to(target)
        with self.assertRaises(SandboxRuntimeError):
            self.collect()

    def test_rejects_symlinked_process_entry(self):
        moved = self.root / "process-real"
        self.process.rename(moved)
        self.process.symlink_to(moved)
        with self.assertRaises(SandboxRuntimeError):
            self.collect()


if __name__ == "__main__":
    unittest.main()
