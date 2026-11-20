import struct
import unittest

from defense.network.pcap.dns import DnsFormatError, parse_dns_message


def qname(name):
    return b"".join(bytes([len(label)]) + label.encode("ascii") for label in name.split(".")) + b"\x00"


class OfflineDnsParserTests(unittest.TestCase):
    def test_parses_question_and_ipv4_answer(self):
        question = qname("example.com") + struct.pack("!HH", 1, 1)
        answer = b"\xc0\x0c" + struct.pack("!HHIH", 1, 1, 60, 4) + b"\xc0\x00\x02\x01"
        payload = struct.pack("!HHHHHH", 0x1234, 0x8180, 1, 1, 0, 0) + question + answer
        message = parse_dns_message(payload)
        self.assertTrue(message.is_response)
        self.assertEqual(message.questions[0].name, "example.com")
        self.assertEqual(message.answers[0].value, "192.0.2.1")
        self.assertEqual(message.answers[0].ttl, 60)

    def test_parses_ipv6_and_compressed_name_answer(self):
        question = qname("example.com") + struct.pack("!HH", 28, 1)
        answer = b"\xc0\x0c" + struct.pack("!HHIH", 28, 1, 10, 16) + bytes.fromhex("20010db8000000000000000000000001")
        payload = struct.pack("!HHHHHH", 1, 0x8180, 1, 1, 0, 0) + question + answer
        self.assertEqual(parse_dns_message(payload).answers[0].value, "2001:db8::1")

    def test_txt_rdata_is_not_exposed(self):
        question = qname("example.com") + struct.pack("!HH", 16, 1)
        txt = b"\x0bsecret=value"
        answer = b"\xc0\x0c" + struct.pack("!HHIH", 16, 1, 1, len(txt)) + txt
        payload = struct.pack("!HHHHHH", 1, 0x8180, 1, 1, 0, 0) + question + answer
        parsed = parse_dns_message(payload)
        self.assertNotIn("secret", repr(parsed))
        self.assertIn("bytes=", parsed.answers[0].value)

    def test_rejects_truncation_trailing_data_and_bad_counts(self):
        for payload in (b"\x00" * 11, b"\x00" * 12 + b"x",
                        struct.pack("!HHHHHH", 1, 0, 0, 0, 0, 0) + b"x"):
            with self.subTest(payload=payload), self.assertRaises(DnsFormatError):
                parse_dns_message(payload)
        excessive = struct.pack("!HHHHHH", 1, 0, 33, 0, 0, 0)
        with self.assertRaises(DnsFormatError):
            parse_dns_message(excessive)

    def test_rejects_pointer_loop_and_invalid_resource_length(self):
        loop = struct.pack("!HHHHHH", 1, 0, 1, 0, 0, 0) + b"\xc0\x0c" + b"\x00" * 4
        with self.assertRaises(DnsFormatError):
            parse_dns_message(loop)
        q = qname("example.com") + struct.pack("!HH", 1, 1)
        bad_answer = b"\xc0\x0c" + struct.pack("!HHIH", 1, 1, 1, 4) + b"\x01"
        malformed = struct.pack("!HHHHHH", 1, 0x8180, 1, 1, 0, 0) + q + bad_answer
        with self.assertRaises(DnsFormatError):
            parse_dns_message(malformed)


if __name__ == "__main__":
    unittest.main()
