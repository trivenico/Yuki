import struct
import unittest

from defense.network.pcap.reader import PcapFormatError, parse_pcap


def capture(magic=b"\xd4\xc3\xb2\xa1", *, snaplen=65535, link_type=1, packets=()):
    order = "<" if magic in (b"\xd4\xc3\xb2\xa1", b"\x4d\x3c\xb2\xa1") else ">"
    resolution = 1_000_000_000 if magic in (b"\x4d\x3c\xb2\xa1", b"\xa1\xb2\x3c\x4d") else 1_000_000
    result = bytearray(magic)
    result.extend(struct.pack(order + "HHIIII", 2, 4, 0, 0, snaplen, link_type))
    for seconds, fraction, data, original in packets:
        result.extend(struct.pack(order + "IIII", seconds, fraction, len(data), original))
        result.extend(data)
    return bytes(result), resolution


class ClassicPcapReaderTests(unittest.TestCase):
    def test_all_endian_and_timestamp_variants(self):
        variants = (
            (b"\xd4\xc3\xb2\xa1", "little", 1_000_000),
            (b"\xa1\xb2\xc3\xd4", "big", 1_000_000),
            (b"\x4d\x3c\xb2\xa1", "little", 1_000_000_000),
            (b"\xa1\xb2\x3c\x4d", "big", 1_000_000_000),
        )
        for magic, byte_order, resolution in variants:
            payload, _ = capture(magic, packets=((123, 456, b"abc", 5),))
            parsed = parse_pcap(payload)
            self.assertEqual(parsed.byte_order, byte_order)
            self.assertEqual(parsed.timestamp_resolution, resolution)
            self.assertEqual(parsed.packets[0].timestamp_seconds, 123)
            self.assertEqual(parsed.packets[0].timestamp_fraction, 456)
            self.assertEqual(parsed.packets[0].data, b"abc")
            self.assertEqual(parsed.packets[0].original_length, 5)

    def test_empty_capture_and_unknown_link_type_are_preserved(self):
        payload, _ = capture(link_type=147)
        parsed = parse_pcap(payload)
        self.assertEqual(parsed.packets, ())
        self.assertEqual(parsed.link_type, 147)

    def test_rejects_bad_magic_version_snaplen_and_linktype_flags(self):
        payload, _ = capture()
        with self.assertRaises(PcapFormatError):
            parse_pcap(b"NOPE" + payload[4:])
        bad_version = bytearray(payload)
        struct.pack_into("<H", bad_version, 4, 3)
        with self.assertRaises(PcapFormatError):
            parse_pcap(bytes(bad_version))
        bad_snaplen, _ = capture(snaplen=0)
        with self.assertRaises(PcapFormatError):
            parse_pcap(bad_snaplen)
        bad_link, _ = capture(link_type=0x10000)
        with self.assertRaises(PcapFormatError):
            parse_pcap(bad_link)

    def test_rejects_truncated_global_record_header_and_packet_data(self):
        payload, _ = capture()
        with self.assertRaises(PcapFormatError):
            parse_pcap(payload[:23])
        with self.assertRaises(PcapFormatError):
            parse_pcap(payload + b"\x00" * 7)
        record_header = struct.pack("<IIII", 1, 0, 4, 4)
        with self.assertRaises(PcapFormatError):
            parse_pcap(payload + record_header + b"ab")

    def test_rejects_bad_fraction_lengths_and_limits(self):
        payload, _ = capture(packets=((1, 1_000_000, b"x", 1),))
        with self.assertRaises(PcapFormatError):
            parse_pcap(payload)
        payload, _ = capture(snaplen=2, packets=((1, 0, b"abc", 3),))
        with self.assertRaises(PcapFormatError):
            parse_pcap(payload)
        payload, _ = capture(packets=((1, 0, b"ab", 1),))
        with self.assertRaises(PcapFormatError):
            parse_pcap(payload)
        payload, _ = capture(packets=((1, 0, b"x", 1),))
        with self.assertRaises(PcapFormatError):
            parse_pcap(payload, max_file_bytes=len(payload) - 1)
        with self.assertRaises(PcapFormatError):
            parse_pcap(payload, max_packet_bytes=1, max_packets=0)

    def test_rejects_packet_count_overflow(self):
        payload, _ = capture(packets=((1, 0, b"a", 1), (2, 0, b"b", 1)))
        with self.assertRaises(PcapFormatError):
            parse_pcap(payload, max_packets=1)

    def test_requires_immutable_bytes(self):
        with self.assertRaises(PcapFormatError):
            parse_pcap(bytearray(b"\x00" * 24))


if __name__ == "__main__":
    unittest.main()
