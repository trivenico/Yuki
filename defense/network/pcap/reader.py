"""Bounded offline reader for classic PCAP files; never captures or transmits packets."""
from __future__ import annotations

import struct
from dataclasses import dataclass

_MAX_FILE_BYTES = 64 * 1024 * 1024
_MAX_PACKET_BYTES = 262_144
_MAX_PACKETS = 100_000

_MAGIC = {
    b"\xd4\xc3\xb2\xa1": ("<", 1_000_000),
    b"\xa1\xb2\xc3\xd4": (">", 1_000_000),
    b"\x4d\x3c\xb2\xa1": ("<", 1_000_000_000),
    b"\xa1\xb2\x3c\x4d": (">", 1_000_000_000),
}


class PcapFormatError(ValueError):
    """Raised when a classic PCAP input is malformed or exceeds parser limits."""


@dataclass(frozen=True)
class PcapPacket:
    index: int
    timestamp_seconds: int
    timestamp_fraction: int
    captured_length: int
    original_length: int
    data: bytes


@dataclass(frozen=True)
class PcapCapture:
    version_major: int
    version_minor: int
    snaplen: int
    link_type: int
    byte_order: str
    timestamp_resolution: int
    packets: tuple[PcapPacket, ...]
    source_size: int


def parse_pcap(
    payload: bytes,
    *,
    max_file_bytes: int = _MAX_FILE_BYTES,
    max_packet_bytes: int = _MAX_PACKET_BYTES,
    max_packets: int = _MAX_PACKETS,
) -> PcapCapture:
    """Parse a complete classic-PCAP byte string with strict resource bounds.

    The caller owns file selection and I/O. This function accepts bytes only,
    performs no filesystem access, and never interprets packet payloads.
    """
    if not isinstance(payload, bytes):
        raise PcapFormatError("PCAP input must be immutable bytes")
    for name, value, upper in (
        ("max_file_bytes", max_file_bytes, _MAX_FILE_BYTES),
        ("max_packet_bytes", max_packet_bytes, _MAX_PACKET_BYTES),
        ("max_packets", max_packets, _MAX_PACKETS),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or value < 1 or value > upper:
            raise PcapFormatError(f"{name} is outside the supported bounds")
    if len(payload) > max_file_bytes:
        raise PcapFormatError("PCAP input exceeds the configured file-size limit")
    if len(payload) < 24:
        raise PcapFormatError("PCAP global header is truncated")

    magic = payload[:4]
    if magic not in _MAGIC:
        raise PcapFormatError("unsupported PCAP magic; classic PCAP is required")
    byte_order, resolution = _MAGIC[magic]
    major, minor, _zone, _sigfigs, snaplen, link_type = struct.unpack_from(
        byte_order + "HHIIII", payload, 4
    )
    if (major, minor) != (2, 4):
        raise PcapFormatError("unsupported classic PCAP version")
    if snaplen < 1 or snaplen > max_packet_bytes:
        raise PcapFormatError("PCAP snaplen is outside the supported packet-size limit")
    if link_type > 0xFFFF:
        raise PcapFormatError("PCAP link type contains unsupported flag bits")

    packets: list[PcapPacket] = []
    offset = 24
    while offset < len(payload):
        if len(packets) >= max_packets:
            raise PcapFormatError("PCAP packet count exceeds the configured limit")
        if len(payload) - offset < 16:
            raise PcapFormatError("PCAP packet record header is truncated")
        seconds, fraction, captured, original = struct.unpack_from(
            byte_order + "IIII", payload, offset
        )
        offset += 16
        if fraction >= resolution:
            raise PcapFormatError("PCAP packet timestamp fraction is out of range")
        if captured > snaplen or captured > max_packet_bytes:
            raise PcapFormatError("PCAP captured length exceeds the configured limit")
        if original < captured:
            raise PcapFormatError("PCAP original length is smaller than captured length")
        if captured > len(payload) - offset:
            raise PcapFormatError("PCAP packet data is truncated")
        packet_data = payload[offset : offset + captured]
        offset += captured
        packets.append(
            PcapPacket(
                index=len(packets),
                timestamp_seconds=seconds,
                timestamp_fraction=fraction,
                captured_length=captured,
                original_length=original,
                data=packet_data,
            )
        )

    return PcapCapture(
        version_major=major,
        version_minor=minor,
        snaplen=snaplen,
        link_type=link_type,
        byte_order="little" if byte_order == "<" else "big",
        timestamp_resolution=resolution,
        packets=tuple(packets),
        source_size=len(payload),
    )
