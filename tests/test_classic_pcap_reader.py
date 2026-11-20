import struct
import unittest

from defense.network.pcap import PcapFormatError, parse_pcap


MAGICS = (
    (b"\xd4\xc3\xb2\xa1", "<", 1_000_000),
    (b"\xa1\xb2\xc3\xd4", ">", 1_000_000),
    (b"\x4d\x3c\xb2\xa1", "<", 1_000_000_000),
    (b"\xa1\xb2\x3c\x4d", ">", 1_000_000_000),
)


def make_pcap(magic, endian, resolution, *, records=None, snaplen=65535, link_type=1):
    header = magic + struct.pack(endian + "HHIIII", 2, 4, 0, 0, snaplen, link_type)
    if records is None:
        records = [(123, resolution - 1, b"abc", 5)]
    body = b"".join(
        struct.pack(endian + "IIII", sec, frac, len(data), original) + data
        for sec, frac, data, original in records
    )
    return header + body


class ClassicPcapReaderTests(unittest.TestCase):
    def test_reads_all_endian_and_timestamp_variants(self):
        for magic, endian, resolution in MAGICS:
            with self.subTest(magic=magic):
                capture = parse_pcap(make_pcap(magic, endian, resolution))
                self.assertEqual(capture.timestamp_resolution, resolution)
                self.assertEqual(capture.byte_order, "little" if endian == "<" else "big")
                self.assertEqual(capture.packets[0].timestamp_seconds, 123)
                self.assertEqual(capture.packets[0].timestamp_fraction, resolution - 1)
                self.assertEqual(capture.packets[0].data, b"abc")
                self.assertEqual(capture.packets[0].original_length, 5)

    def test_empty_capture_is_valid(self):
        magic, endian, _ = MAGICS[0]
        self.assertEqual(parse_pcap(make_pcap(magic, endian, 1_000_000, records=[])).packets, ())

    def test_preserves_unknown_link_type_without_decoding(self):
        magic, endian, resolution = MAGICS[0]
        capture = parse_pcap(make_pcap(magic, endian, resolution, link_type=147))
        self.assertEqual(capture.link_type, 147)
        self.assertEqual(capture.packets[0].data, b"abc")

    def test_rejects_non_bytes_and_bad_magic(self):
        with self.assertRaises(PcapFormatError):
            parse_pcap(bytearray(b"x" * 24))
        with self.assertRaises(PcapFormatError):
            parse_pcap(b"nope" + b"\0" * 20)

    def test_rejects_truncated_global_header_and_unsupported_version(self):
        with self.assertRaises(PcapFormatError):
            parse_pcap(b"\xd4\xc3\xb2\xa1" + b"\0" * 19)
        magic, endian, _ = MAGICS[0]
        with self.assertRaises(PcapFormatError):
            parse_pcap(magic + struct.pack(endian + "HHIIII", 3, 0, 0, 0, 100, 1))

    def test_rejects_invalid_snaplen_and_link_type_flags(self):
        magic, endian, resolution = MAGICS[0]
        with self.assertRaises(PcapFormatError):
            parse_pcap(make_pcap(magic, endian, resolution, snaplen=0))
        with self.assertRaises(PcapFormatError):
            parse_pcap(make_pcap(magic, endian, resolution, snaplen=262145))
        with self.assertRaises(PcapFormatError):
            parse_pcap(make_pcap(magic, endian, resolution, link_type=0x10000))

    def test_rejects_timestamp_fraction_out_of_range(self):
        magic, endian, resolution = MAGICS[0]
        with self.assertRaises(PcapFormatError):
            parse_pcap(make_pcap(magic, endian, resolution, records=[(1, resolution, b"", 0)]))

    def test_rejects_captured_length_over_snaplen_and_original_smaller(self):
        magic, endian, resolution = MAGICS[0]
        with self.assertRaises(PcapFormatError):
            parse_pcap(make_pcap(magic, endian, resolution, snaplen=2, records=[(1, 0, b"abc", 3)]))
        with self.assertRaises(PcapFormatError):
            parse_pcap(make_pcap(magic, endian, resolution, records=[(1, 0, b"abc", 2)]))

    def test_rejects_truncated_record_header_and_packet_data(self):
        magic, endian, resolution = MAGICS[0]
        base = make_pcap(magic, endian, resolution, records=[])
        with self.assertRaises(PcapFormatError):
            parse_pcap(base + b"\0" * 15)
        record_header = struct.pack(endian + "IIII", 1, 0, 4, 4)
        with self.assertRaises(PcapFormatError):
            parse_pcap(base + record_header + b"abc")

    def test_enforces_file_packet_and_count_limits(self):
        magic, endian, resolution = MAGICS[0]
        payload = make_pcap(magic, endian, resolution)
        with self.assertRaises(PcapFormatError):
            parse_pcap(payload, max_file_bytes=24)
        with self.assertRaises(PcapFormatError):
            parse_pcap(payload, max_packet_bytes=2)
        multi = make_pcap(magic, endian, resolution, records=[(1, 0, b"a", 1), (2, 0, b"b", 1)])
        with self.assertRaises(PcapFormatError):
            parse_pcap(multi, max_packets=1)

    def test_rejects_invalid_limit_configuration(self):
        magic, endian, resolution = MAGICS[0]
        payload = make_pcap(magic, endian, resolution)
        for kwargs in ({"max_file_bytes": 0}, {"max_packet_bytes": True}, {"max_packets": 100001}):
            with self.subTest(kwargs=kwargs), self.assertRaises(PcapFormatError):
                parse_pcap(payload, **kwargs)


if __name__ == "__main__":
    unittest.main()
