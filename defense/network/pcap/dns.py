"""Bounded DNS message metadata parser; no resolution or network access."""
from __future__ import annotations

import ipaddress
import struct
from dataclasses import dataclass


class DnsFormatError(ValueError):
    """Raised when a DNS message is malformed or exceeds configured bounds."""


@dataclass(frozen=True)
class DnsQuestion:
    name: str
    record_type: int
    record_class: int


@dataclass(frozen=True)
class DnsRecord:
    name: str
    record_type: int
    record_class: int
    ttl: int
    value: str


@dataclass(frozen=True)
class DnsMessage:
    transaction_id: int
    is_response: bool
    opcode: int
    response_code: int
    truncated: bool
    recursion_desired: bool
    recursion_available: bool
    questions: tuple[DnsQuestion, ...]
    answers: tuple[DnsRecord, ...]
    authorities: tuple[DnsRecord, ...]
    additionals: tuple[DnsRecord, ...]


def _name(data: bytes, offset: int, *, max_jumps: int = 16) -> tuple[str, int]:
    labels: list[str] = []
    cursor = offset
    consumed = None
    visited: set[int] = set()
    jumps = 0
    while True:
        if cursor >= len(data):
            raise DnsFormatError("DNS name exceeds message")
        length = data[cursor]
        if length & 0xC0 == 0xC0:
            if cursor + 1 >= len(data):
                raise DnsFormatError("truncated DNS compression pointer")
            pointer = ((length & 0x3F) << 8) | data[cursor + 1]
            if pointer >= len(data) or pointer in visited:
                raise DnsFormatError("invalid or cyclic DNS compression pointer")
            if consumed is None:
                consumed = cursor + 2
            visited.add(pointer)
            jumps += 1
            if jumps > max_jumps:
                raise DnsFormatError("DNS compression depth exceeded")
            cursor = pointer
            continue
        if length & 0xC0:
            raise DnsFormatError("reserved DNS label encoding")
        cursor += 1
        if length == 0:
            if consumed is None:
                consumed = cursor
            break
        if length > 63 or cursor + length > len(data):
            raise DnsFormatError("invalid DNS label length")
        raw = data[cursor:cursor + length]
        if any(byte < 0x21 or byte > 0x7E or byte in (ord("."), ord("\\")) for byte in raw):
            raise DnsFormatError("non-printable or ambiguous DNS label")
        labels.append(raw.decode("ascii").lower())
        cursor += length
        if sum(len(part) + 1 for part in labels) > 254:
            raise DnsFormatError("DNS name too long")
    return (".".join(labels) or "."), consumed


def _records(data: bytes, offset: int, count: int, *, max_records: int) -> tuple[tuple[DnsRecord, ...], int]:
    result: list[DnsRecord] = []
    if count > max_records:
        raise DnsFormatError("DNS record count exceeded")
    for _ in range(count):
        name, offset = _name(data, offset)
        if offset + 10 > len(data):
            raise DnsFormatError("truncated DNS resource record")
        kind, klass, ttl, length = struct.unpack_from("!HHIH", data, offset)
        offset += 10
        end = offset + length
        if end > len(data):
            raise DnsFormatError("DNS resource data exceeds message")
        raw = data[offset:end]
        value = f"rdata_length={length}"
        if kind == 1 and length == 4:
            value = str(ipaddress.IPv4Address(raw))
        elif kind == 28 and length == 16:
            value = str(ipaddress.IPv6Address(raw))
        elif kind in (2, 5, 12):
            decoded, consumed = _name(data, offset)
            if consumed != end:
                raise DnsFormatError("DNS name RDATA length mismatch")
            value = decoded
        elif kind == 16:
            # TXT contents can carry secrets or personal data: retain only lengths.
            value = f"txt_segments={raw[0] if raw else 0};bytes={length}"
        result.append(DnsRecord(name, kind, klass, ttl, value))
        offset = end
    return tuple(result), offset


def parse_dns_message(data: bytes, *, max_message_bytes: int = 4096,
                      max_questions: int = 32, max_records: int = 128) -> DnsMessage:
    if not isinstance(data, bytes) or len(data) < 12 or len(data) > max_message_bytes:
        raise DnsFormatError("DNS message must be immutable bytes within bounds")
    if any(isinstance(x, bool) or not isinstance(x, int) or x < 0 for x in
           (max_message_bytes, max_questions, max_records)):
        raise DnsFormatError("invalid parser limits")
    ident, flags, qd, an, ns, ar = struct.unpack_from("!HHHHHH", data)
    if qd > max_questions or an + ns + ar > max_records:
        raise DnsFormatError("DNS section count exceeded")
    offset = 12
    questions: list[DnsQuestion] = []
    for _ in range(qd):
        name, offset = _name(data, offset)
        if offset + 4 > len(data):
            raise DnsFormatError("truncated DNS question")
        kind, klass = struct.unpack_from("!HH", data, offset)
        offset += 4
        questions.append(DnsQuestion(name, kind, klass))
    answers, offset = _records(data, offset, an, max_records=max_records)
    authorities, offset = _records(data, offset, ns, max_records=max_records)
    additionals, offset = _records(data, offset, ar, max_records=max_records)
    if offset != len(data):
        raise DnsFormatError("trailing bytes after DNS message")
    return DnsMessage(
        transaction_id=ident,
        is_response=bool(flags & 0x8000),
        opcode=(flags >> 11) & 0xF,
        response_code=flags & 0xF,
        truncated=bool(flags & 0x0200),
        recursion_desired=bool(flags & 0x0100),
        recursion_available=bool(flags & 0x0080),
        questions=tuple(questions),
        answers=answers,
        authorities=authorities,
        additionals=additionals,
    )
