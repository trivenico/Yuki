import struct
import unittest

from defense.network.pcap import reader


class RustPcapGuardParityTests(unittest.TestCase):
    @staticmethod
    def capture():
        header = struct.pack("<IHHIIII", 0xA1B2C3D4, 2, 4, 0, 0, 65535, 1)
        packet = b"hello"
        record = struct.pack("<IIII", 123, 456, len(packet), len(packet)) + packet
        return header + record

    def test_native_parser_matches_python_capture_contract(self):
        if reader._rust_parse_pcap is None:
            self.skipTest("optional Rust extension is not installed")
        payload = self.capture()
        native = reader.parse_pcap(payload)
        rust = reader._rust_parse_pcap(payload, 64 * 1024 * 1024, 262144, 100000)
        major, minor, snaplen, link_type, order, resolution, records = rust
        self.assertEqual((major, minor, snaplen, link_type, order, resolution),
                         (native.version_major, native.version_minor, native.snaplen,
                          native.link_type, native.byte_order, native.timestamp_resolution))
        self.assertEqual(records, [(p.timestamp_seconds, p.timestamp_fraction,
                                    p.captured_length, p.original_length, p.data)
                                   for p in native.packets])

    def test_native_and_python_reject_truncated_packet_payload(self):
        if reader._rust_parse_pcap is None:
            self.skipTest("optional Rust extension is not installed")
        malformed = self.capture()[:-1]
        with self.assertRaises(reader.PcapFormatError):
            reader.parse_pcap(malformed)
        with self.assertRaises(ValueError):
            reader._rust_parse_pcap(malformed, 64 * 1024 * 1024, 262144, 100000)


if __name__ == "__main__":
    unittest.main()
