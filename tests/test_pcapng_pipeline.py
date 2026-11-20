import struct
import unittest

from defense.network.pcap.pipeline import analyze_capture
from defense.network.pcap.pcapng import parse_pcapng


def _block(kind, body):
    length = 12 + len(body)
    if length % 4:
        body += b"\\x00" * ((-length) % 4)
        length = 12 + len(body)
    return struct.pack("<II", kind, length) + body + struct.pack("<I", length)


def _capture(dns):
    eth = b"\\x00" * 12 + struct.pack("!H", 0x0800)
    udp = struct.pack("!HHHH", 53000, 53, 8 + len(dns), 0) + dns
    ip = struct.pack("!BBHHHBBH4s4s", 0x45, 0, 20 + len(udp), 1, 0, 64, 17, 0,
                     b"\\x0a\\x00\\x00\\x01", b"\\x0a\\x00\\x00\\x02") + udp
    packet = eth + ip
    shb = b"\\x4d\\x3c\\x2b\\x1a" + struct.pack("<HHq", 1, 0, -1)
    idb = struct.pack("<HHI", 1, 0, 65535)
    epb = struct.pack("<IIIII", 0, 0, 1_500_000, len(packet), len(packet))
    epb += packet + b"\\x00" * ((-len(packet)) % 4)
    return _block(0x0A0D0D0A, shb) + _block(1, idb) + _block(6, epb)


class PcapNgPipelineTests(unittest.TestCase):
    def test_pcapng_packets_use_shared_dns_analysis_pipeline(self):
        dns = struct.pack("!HHHHHH", 0x1234, 0x0100, 0, 0, 0, 0)
        parsed = parse_pcapng(_capture(dns))
        result = analyze_capture(parsed)
        self.assertEqual(result.packet_count, 1)
        self.assertEqual(result.dns_count, 1)
        self.assertEqual(result.packets[0].dns.transaction_id, 0x1234)
        self.assertEqual(result.packets[0].timestamp, 1.5)


if __name__ == "__main__":
    unittest.main()
