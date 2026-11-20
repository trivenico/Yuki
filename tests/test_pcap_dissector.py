import struct
import unittest

from defense.network.pcap.dissector import DissectionError, dissect_packet


def ethernet(payload, ethertype):
    return b"\x00\x11\x22\x33\x44\x55\x66\x77\x88\x99\xaa\xbb" + struct.pack("!H", ethertype) + payload


def ipv4(payload, proto=17, source=b"\x0a\x00\x00\x01", dest=b"\x0a\x00\x00\x02"):
    header = struct.pack("!BBHHHBBH4s4s", 0x45, 0, 20 + len(payload), 1, 0, 64, proto, 0, source, dest)
    return header + payload


class OfflineDissectorTests(unittest.TestCase):
    def test_decodes_ethernet_ipv4_udp_metadata(self):
        udp = struct.pack("!HHHH", 53000, 53, 8, 0)
        result = dissect_packet(ethernet(ipv4(udp), 0x0800), link_type=1)
        self.assertEqual((result.network_protocol, result.transport_protocol), ("ipv4", "udp"))
        self.assertEqual((result.source, result.destination), ("10.0.0.1", "10.0.0.2"))
        self.assertEqual((result.source_port, result.destination_port), (53000, 53))
        self.assertEqual(result.payload_length, 0)

    def test_decodes_ipv6_tcp_header_only(self):
        tcp = struct.pack("!HHIIHHHH", 1234, 443, 0, 0, 5 << 12, 0, 0, 0)
        ipv6 = struct.pack("!IHBB16s16s", 6 << 28, len(tcp), 6, 64,
                           bytes.fromhex("20010db8000000000000000000000001"),
                           bytes.fromhex("20010db8000000000000000000000002")) + tcp
        result = dissect_packet(ethernet(ipv6, 0x86DD), link_type=1)
        self.assertEqual(result.network_protocol, "ipv6")
        self.assertEqual(result.transport_protocol, "tcp")
        self.assertEqual(result.destination_port, 443)
        self.assertEqual(result.tcp_sequence, 0)
        self.assertEqual(result.tcp_acknowledgment, 0)
        self.assertEqual(result.tcp_flags, 0)
        self.assertIsNotNone(result.payload_offset)

    def test_decodes_arp_and_vlan(self):
        arp = struct.pack("!HHBBH6s4s6s4s", 1, 0x0800, 6, 4, 1,
                          b"abcdef", b"\x0a\x00\x00\x01", b"ghijkl", b"\x0a\x00\x00\x02")
        tagged = b"\x00" * 12 + struct.pack("!H", 0x8100) + struct.pack("!HH", 7, 0x0806) + arp
        result = dissect_packet(tagged, link_type=1)
        self.assertEqual(result.network_protocol, "arp")
        self.assertIn("vlan_id=7", result.details)

    def test_non_initial_fragment_and_unknown_link_type_are_metadata_only(self):
        fragment = bytearray(ipv4(b"abcd"))
        struct.pack_into("!H", fragment, 6, 1)
        result = dissect_packet(ethernet(bytes(fragment), 0x0800), link_type=1)
        self.assertIn("non_initial_fragment", result.details)
        unknown = dissect_packet(b"secret", link_type=147)
        self.assertEqual(unknown.network_protocol, "unsupported_link_type")
        self.assertFalse(hasattr(unknown, "data"))

    def test_rejects_truncated_and_malformed_headers(self):
        for data in (b"", b"\x00" * 10, ethernet(b"\x45", 0x0800),
                     ethernet(ipv4(b"\x00" * 7), 0x0800)):
            with self.subTest(data=data), self.assertRaises(DissectionError):
                dissect_packet(data, link_type=1)

    def test_rejects_invalid_packet_type_and_bounds(self):
        with self.assertRaises(DissectionError):
            dissect_packet(bytearray(b"x"), link_type=1)
        with self.assertRaises(DissectionError):
            dissect_packet(b"x" * 20, link_type=1, max_packet_bytes=10)


if __name__ == "__main__":
    unittest.main()
