import struct
import unittest

from defense.network.pcap.pcapng import PcapNgFormatError, parse_pcapng


def block(order, kind, body):
    length = 12 + len(body)
    if length % 4:
        body += b"\x00" * ((-length) % 4)
        length = 12 + len(body)
    return struct.pack(order + "I", kind) + struct.pack(order + "I", length) + body + struct.pack(order + "I", length)


def section(order="<"):
    bom = b"\x4d\x3c\x2b\x1a" if order == "<" else b"\x1a\x2b\x3c\x4d"
    body = bom + struct.pack(order + "HHq", 1, 0, -1)
    return block(order, 0x0A0D0D0A, body)


def interface(order="<", link_type=1, snaplen=65535, options=b""):
    return block(order, 1, struct.pack(order + "HHI", link_type, 0, snaplen) + options)


def enhanced(order, data, ticks=1_500_000, interface_id=0, original=None):
    original = len(data) if original is None else original
    padded = data + b"\x00" * ((-len(data)) % 4)
    body = struct.pack(order + "IIIII", interface_id, ticks >> 32, ticks & 0xffffffff,
                       len(data), original) + padded
    return block(order, 6, body)


class PcapNgReaderTests(unittest.TestCase):
    def test_reads_little_and_big_endian_sections(self):
        for order in ("<", ">"):
            parsed = parse_pcapng(section(order) + interface(order) + enhanced(order, b"abcd"))
            self.assertEqual(parsed.section_count, 1)
            self.assertEqual(parsed.interface_count, 1)
            self.assertEqual(parsed.packets[0].data, b"abcd")
            self.assertEqual(parsed.packets[0].link_type, 1)
            self.assertEqual(parsed.packets[0].timestamp, 1.5)

    def test_applies_binary_timestamp_resolution_and_offset(self):
        options = struct.pack("<HHB3x", 9, 1, 0x8A) + struct.pack("<HHq", 14, 8, 2) + struct.pack("<HH", 0, 0)
        parsed = parse_pcapng(section() + interface(options=options) + enhanced("<", b"x", ticks=1024))
        self.assertEqual(parsed.packets[0].timestamp, 3.0)

    def test_supports_multiple_interfaces_and_link_types(self):
        parsed = parse_pcapng(section() + interface(link_type=1) + interface(link_type=101) +
                              enhanced("<", b"one", interface_id=0) +
                              enhanced("<", b"two", interface_id=1))
        self.assertEqual([p.link_type for p in parsed.packets], [1, 101])

    def test_rejects_bad_magic_lengths_truncation_and_trailer(self):
        good = section() + interface() + enhanced("<", b"x")
        with self.assertRaises(PcapNgFormatError):
            parse_pcapng(b"not-pcapng")
        with self.assertRaises(PcapNgFormatError):
            parse_pcapng(good[:-1])
        broken = bytearray(good)
        broken[-4:] = b"\x00\x00\x00\x00"
        with self.assertRaises(PcapNgFormatError):
            parse_pcapng(bytes(broken))

    def test_rejects_invalid_interface_and_packet_bounds(self):
        with self.assertRaises(PcapNgFormatError):
            parse_pcapng(section() + interface(snaplen=0))
        with self.assertRaises(PcapNgFormatError):
            parse_pcapng(section() + interface() + enhanced("<", b"abc", interface_id=3))
        with self.assertRaises(PcapNgFormatError):
            parse_pcapng(section() + interface(snaplen=2) + enhanced("<", b"abc"))

    def test_rejects_unsupported_simple_packet_block(self):
        with self.assertRaises(PcapNgFormatError):
            parse_pcapng(section() + interface() + block("<", 3, struct.pack("<I", 10)))

    def test_enforces_file_block_packet_and_interface_limits(self):
        good = section() + interface() + enhanced("<", b"x")
        with self.assertRaises(PcapNgFormatError):
            parse_pcapng(good, max_file_bytes=len(good) - 1)
        with self.assertRaises(PcapNgFormatError):
            parse_pcapng(good, max_packets=1) if False else parse_pcapng(
                section() + interface() + enhanced("<", b"x") + enhanced("<", b"y"), max_packets=1)
        with self.assertRaises(PcapNgFormatError):
            parse_pcapng(section() + interface() + interface(), max_interfaces=1)

    def test_requires_immutable_bytes(self):
        with self.assertRaises(PcapNgFormatError):
            parse_pcapng(bytearray(section()))


if __name__ == "__main__":
    unittest.main()
