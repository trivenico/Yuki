import struct
import unittest
from unittest.mock import patch

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
        with patch.object(reader, "_rust_parse_pcap", None):
            python_capture = reader.parse_pcap(payload)
        rust = reader._rust_parse_pcap(payload, 64 * 1024 * 1024, 262144, 100000)
        major, minor, snaplen, link_type, order, resolution, records = rust
        self.assertEqual((major, minor, snaplen, link_type, order, resolution),
                         (python_capture.version_major, python_capture.version_minor,
                          python_capture.snaplen, python_capture.link_type,
                          python_capture.byte_order, python_capture.timestamp_resolution))
        self.assertEqual(records, [(p.timestamp_seconds, p.timestamp_fraction,
                                    p.captured_length, p.original_length, p.data)
                                   for p in python_capture.packets])

    def test_native_and_python_reject_truncated_packet_payload(self):
        if reader._rust_parse_pcap is None:
            self.skipTest("optional Rust extension is not installed")
        malformed = self.capture()[:-1]
        with self.assertRaises(reader.PcapFormatError):
            reader.parse_pcap(malformed)
        with self.assertRaises(ValueError):
            reader._rust_parse_pcap(malformed, 64 * 1024 * 1024, 262144, 100000)

    def test_native_parser_matches_big_endian_and_nanosecond_captures(self):
        if reader._rust_parse_pcap is None:
            self.skipTest("optional Rust extension is not installed")
        cases = [
            (">", 0xA1B2C3D4, 1_000_000),
            ("<", 0xA1B23C4D, 1_000_000_000),
            (">", 0xA1B23C4D, 1_000_000_000),
        ]
        for endian, magic, resolution in cases:
            with self.subTest(endian=endian, magic=hex(magic)):
                packet = b"binary\\x00payload"
                payload = (
                    struct.pack(endian + "IHHIIII", magic, 2, 4, 0, 0, 65535, 1)
                    + struct.pack(endian + "IIII", 7, resolution - 1, len(packet), len(packet))
                    + packet
                )
                with patch.object(reader, "_rust_parse_pcap", None):
                    python_capture = reader.parse_pcap(payload)
                rust = reader._rust_parse_pcap(payload, 64 * 1024 * 1024, 262144, 100000)
                major, minor, snaplen, link_type, order, timestamp_resolution, records = rust
                self.assertEqual(
                    (major, minor, snaplen, link_type, order, timestamp_resolution),
                    (python_capture.version_major, python_capture.version_minor,
                     python_capture.snaplen, python_capture.link_type,
                     python_capture.byte_order, python_capture.timestamp_resolution),
                )
                self.assertEqual(
                    records,
                    [(p.timestamp_seconds, p.timestamp_fraction, p.captured_length,
                      p.original_length, p.data) for p in python_capture.packets],
                )

    def test_native_parser_matches_python_rejections_for_header_and_length_errors(self):
        if reader._rust_parse_pcap is None:
            self.skipTest("optional Rust extension is not installed")
        valid = self.capture()
        malformed = [
            valid[:23],
            valid[:4] + struct.pack("<HHIIII", 1, 4, 0, 0, 65535, 1),
            valid[:16] + struct.pack("<II", 0, 0x10000) + valid[24:],
            valid[:-1],
        ]
        for payload in malformed:
            with self.subTest(payload_length=len(payload)):
                with patch.object(reader, "_rust_parse_pcap", None):
                    with self.assertRaises(reader.PcapFormatError):
                        reader.parse_pcap(payload)
                with self.assertRaises(ValueError):
                    reader._rust_parse_pcap(
                        payload, 64 * 1024 * 1024, 262144, 100000
                    )


if __name__ == "__main__":
    unittest.main()
