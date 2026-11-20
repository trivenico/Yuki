"""Bounded TLS ClientHello metadata parser for offline packet analysis."""
from __future__ import annotations

import struct
from dataclasses import dataclass


class TlsFormatError(ValueError):
    """Raised when a TLS record or ClientHello is malformed or out of bounds."""


@dataclass(frozen=True)
class TlsClientHello:
    legacy_version: int
    supported_versions: tuple[int, ...]
    cipher_suites: tuple[int, ...]
    server_name: str | None
    alpn_protocols: tuple[str, ...]
    session_id_length: int
    compression_methods: tuple[int, ...]


def _u16(data: bytes, offset: int) -> int:
    if offset + 2 > len(data):
        raise TlsFormatError("truncated TLS length field")
    return struct.unpack_from("!H", data, offset)[0]


def parse_tls_client_hello(data: bytes, *, max_record_bytes: int = 18_432) -> TlsClientHello:
    """Parse one TLS handshake record containing one ClientHello; never return payload bytes."""
    if not isinstance(data, bytes) or len(data) < 9 or len(data) > max_record_bytes + 5:
        raise TlsFormatError("TLS record must be immutable bytes within bounds")
    if data[0] != 22:
        raise TlsFormatError("not a TLS handshake record")
    record_version = _u16(data, 1)
    record_length = _u16(data, 3)
    if record_length > max_record_bytes or record_length != len(data) - 5:
        raise TlsFormatError("invalid or truncated TLS record length")
    body = data[5:]
    if body[0] != 1:
        raise TlsFormatError("TLS handshake is not ClientHello")
    hello_length = int.from_bytes(body[1:4], "big")
    if hello_length != len(body) - 4:
        raise TlsFormatError("ClientHello length mismatch")
    hello = body[4:]
    if len(hello) < 34:
        raise TlsFormatError("truncated ClientHello fixed fields")
    legacy_version = _u16(hello, 0)
    offset = 34  # version + random
    session_length = hello[offset]
    offset += 1
    if session_length > 32 or offset + session_length + 2 > len(hello):
        raise TlsFormatError("invalid session ID length")
    offset += session_length
    cipher_length = _u16(hello, offset)
    offset += 2
    if cipher_length < 2 or cipher_length % 2 or offset + cipher_length + 1 > len(hello):
        raise TlsFormatError("invalid cipher suite vector")
    ciphers = tuple(struct.unpack_from("!H", hello, i)[0] for i in range(offset, offset + cipher_length, 2))
    offset += cipher_length
    compression_length = hello[offset]
    offset += 1
    if compression_length < 1 or offset + compression_length > len(hello):
        raise TlsFormatError("invalid compression methods vector")
    compression = tuple(hello[offset:offset + compression_length])
    offset += compression_length
    if offset == len(hello):
        return TlsClientHello(legacy_version, (), ciphers, None, (), session_length, compression)
    extensions_length = _u16(hello, offset)
    offset += 2
    if extensions_length != len(hello) - offset:
        raise TlsFormatError("TLS extension vector length mismatch")
    extensions_end = offset + extensions_length
    seen: set[int] = set()
    server_name = None
    versions: tuple[int, ...] = ()
    alpns: tuple[str, ...] = ()
    while offset < extensions_end:
        if offset + 4 > extensions_end:
            raise TlsFormatError("truncated TLS extension header")
        kind, length = struct.unpack_from("!HH", hello, offset)
        offset += 4
        end = offset + length
        if end > extensions_end or kind in seen:
            raise TlsFormatError("invalid or duplicate TLS extension")
        seen.add(kind)
        value = hello[offset:end]
        if kind == 0:  # server_name
            if len(value) < 5:
                raise TlsFormatError("truncated SNI extension")
            list_length = _u16(value, 0)
            if list_length != len(value) - 2:
                raise TlsFormatError("SNI list length mismatch")
            pos = 2
            while pos < len(value):
                if pos + 3 > len(value):
                    raise TlsFormatError("truncated SNI name")
                name_type = value[pos]
                name_length = _u16(value, pos + 1)
                pos += 3
                if name_length == 0 or pos + name_length > len(value):
                    raise TlsFormatError("invalid SNI hostname length")
                raw = value[pos:pos + name_length]
                pos += name_length
                if name_type == 0:
                    try:
                        candidate = raw.decode("ascii").lower()
                    except UnicodeDecodeError as exc:
                        raise TlsFormatError("non-ASCII SNI hostname") from exc
                    if len(candidate) > 253 or not all(
                        label and len(label) <= 63 and label[0] != "-" and label[-1] != "-"
                        and all(ch.isalnum() or ch == "-" for ch in label)
                        for label in candidate.rstrip(".").split(".")
                    ):
                        raise TlsFormatError("invalid SNI hostname")
                    if server_name is not None:
                        raise TlsFormatError("multiple host_name entries")
                    server_name = candidate.rstrip(".")
        elif kind == 16:  # ALPN
            if len(value) < 3:
                raise TlsFormatError("truncated ALPN extension")
            list_length = _u16(value, 0)
            if list_length != len(value) - 2:
                raise TlsFormatError("ALPN list length mismatch")
            pos = 2
            protocols = []
            while pos < len(value):
                size = value[pos]
                pos += 1
                if size == 0 or pos + size > len(value):
                    raise TlsFormatError("invalid ALPN protocol length")
                try:
                    protocols.append(value[pos:pos + size].decode("ascii"))
                except UnicodeDecodeError as exc:
                    raise TlsFormatError("non-ASCII ALPN protocol") from exc
                pos += size
            alpns = tuple(protocols)
        elif kind == 43:  # supported_versions
            if len(value) < 3 or value[0] != len(value) - 1 or value[0] % 2:
                raise TlsFormatError("invalid supported_versions extension")
            versions = tuple(struct.unpack_from("!H", value, i)[0] for i in range(1, len(value), 2))
        offset = end
    if offset != extensions_end:
        raise TlsFormatError("TLS extension parsing did not end at boundary")
    return TlsClientHello(legacy_version, versions, ciphers, server_name, alpns,
                          session_length, compression)
