import struct
import unittest

from defense.network.pcap.pipeline import analyze_capture
from defense.network.pcap.reader import PcapCapture, PcapPacket


def ethernet(payload, ethertype=0x0800):
    return b"\x00" * 12 + struct.pack("!H", ethertype) + payload


def ipv4(payload, protocol):
    return struct.pack("!BBHHHBBH4s4s", 0x45, 0, 20 + len(payload), 1, 0, 64,
                       protocol, 0, b"\x0a\x00\x00\x01", b"\x0a\x00\x00\x02") + payload


def udp_packet(payload, source=53000, destination=53):
    udp = struct.pack("!HHHH", source, destination, 8 + len(payload), 0) + payload
    return ethernet(ipv4(udp, 17))


def tcp_packet(sequence, payload, flags=0x18):
    tcp = struct.pack("!HHIIHHHH", 50000, 443, sequence, 0,
                      (5 << 12) | flags, 65535, 0, 0) + payload
    return ethernet(ipv4(tcp, 6))


def tls_client_hello():
    hello = (struct.pack("!H", 0x0303) + b"r" * 32 + b"\x00" +
             struct.pack("!H", 2) + b"\x13\x01" + b"\x01\x00" + b"\x00\x00")
    handshake = b"\x01" + len(hello).to_bytes(3, "big") + hello
    return b"\x16\x03\x01" + struct.pack("!H", len(handshake)) + handshake


class OfflineCapturePipelineTests(unittest.TestCase):
    def capture(self, packets):
        return PcapCapture(2, 4, 65535, 1, "little", 1_000_000,
                           tuple(PcapPacket(i, i + 1, 0, len(p), len(p), p)
                                 for i, p in enumerate(packets)), sum(map(len, packets)))

    def test_dns_is_parsed_from_selected_udp_payload(self):
        dns = struct.pack("!HHHHHH", 7, 0x0100, 0, 0, 0, 0)
        result = analyze_capture(self.capture([udp_packet(dns)]))
        self.assertEqual(result.dns_count, 1)
        self.assertEqual(result.packets[0].dns.transaction_id, 7)
        self.assertFalse(hasattr(result.packets[0], "payload"))

    def test_tls_clienthello_is_parsed_from_syn_initialized_tcp_stream(self):
        hello = tls_client_hello()
        packets = [tcp_packet(100, b"", flags=0x02), tcp_packet(101, hello)]
        result = analyze_capture(self.capture(packets))
        self.assertEqual(result.tls_client_hello_count, 1)
        self.assertEqual(result.packets[1].tls_client_hello.legacy_version, 0x0303)

    def test_out_of_order_tcp_segments_are_reassembled(self):
        hello = tls_client_hello()
        split = 12
        packets = [tcp_packet(100, b"", flags=0x02),
                   tcp_packet(101 + split, hello[split:]),
                   tcp_packet(101, hello[:split])]
        result = analyze_capture(self.capture(packets))
        self.assertEqual(result.tls_client_hello_count, 1)

    def test_ignores_non_dns_udp_and_tcp_without_syn(self):
        result = analyze_capture(self.capture([
            udp_packet(b"not dns", source=1000, destination=1001),
            tcp_packet(10, tls_client_hello()),
        ]))
        self.assertEqual(result.dns_count, 0)
        self.assertEqual(result.tls_client_hello_count, 0)

    def test_rejects_capture_packet_count_over_configured_limit(self):
        with self.assertRaises(ValueError):
            analyze_capture(self.capture([udp_packet(b""), udp_packet(b"")]),
                            max_analyzed_packets=1)


if __name__ == "__main__":
    unittest.main()
