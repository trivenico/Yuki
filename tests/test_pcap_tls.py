import struct
import unittest

from defense.network.pcap.tls import TlsFormatError, parse_tls_client_hello


def extension(kind, value):
    return struct.pack("!HH", kind, len(value)) + value


def client_hello():
    sni_name = b"example.com"
    sni = struct.pack("!H", len(sni_name) + 3) + b"\x00" + struct.pack("!H", len(sni_name)) + sni_name
    alpn = b"\x00\x0c" + b"\x02h2" + b"\x08http/1.1"
    versions = b"\x04\x03\x04\x03\x03"
    extensions = extension(0, sni) + extension(16, alpn) + extension(43, versions)
    hello = (struct.pack("!H", 0x0303) + b"r" * 32 + b"\x00" +
             struct.pack("!H", 4) + b"\x13\x01\x13\x02" +
             b"\x01\x00" + struct.pack("!H", len(extensions)) + extensions)
    handshake = b"\x01" + len(hello).to_bytes(3, "big") + hello
    return b"\x16\x03\x01" + struct.pack("!H", len(handshake)) + handshake


class OfflineTlsClientHelloTests(unittest.TestCase):
    def test_extracts_sni_alpn_versions_and_cipher_metadata(self):
        result = parse_tls_client_hello(client_hello())
        self.assertEqual(result.server_name, "example.com")
        self.assertEqual(result.alpn_protocols, ("h2", "http/1.1"))
        self.assertEqual(result.supported_versions, (0x0304, 0x0303))
        self.assertEqual(result.cipher_suites, (0x1301, 0x1302))

    def test_rejects_wrong_record_truncation_and_length_mismatch(self):
        for payload in (b"\x17" + client_hello()[1:], client_hello()[:-1],
                        client_hello()[:3] + b"\xff\xff" + client_hello()[5:]):
            with self.subTest(payload=payload[:8]), self.assertRaises(TlsFormatError):
                parse_tls_client_hello(payload)

    def test_rejects_duplicate_extension_and_bad_sni(self):
        bad_sni = b"\x00\x05\x00\x00\x02-x"
        exts = extension(0, bad_sni) + extension(0, bad_sni)
        hello = (struct.pack("!H", 0x0303) + b"r" * 32 + b"\x00" +
                 struct.pack("!H", 2) + b"\x13\x01" + b"\x01\x00" +
                 struct.pack("!H", len(exts)) + exts)
        handshake = b"\x01" + len(hello).to_bytes(3, "big") + hello
        record = b"\x16\x03\x01" + struct.pack("!H", len(handshake)) + handshake
        with self.assertRaises(TlsFormatError):
            parse_tls_client_hello(record)

    def test_rejects_mutable_input_and_oversize(self):
        with self.assertRaises(TlsFormatError):
            parse_tls_client_hello(bytearray(client_hello()))
        with self.assertRaises(TlsFormatError):
            parse_tls_client_hello(client_hello(), max_record_bytes=10)


if __name__ == "__main__":
    unittest.main()
