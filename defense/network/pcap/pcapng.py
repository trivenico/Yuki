"""Strict offline reader for the supported PCAPNG block subset."""
from __future__ import annotations

import struct
from dataclasses import dataclass

_MAX_FILE_BYTES = 64 * 1024 * 1024
_MAX_BLOCK_BYTES = 262_144
_MAX_PACKETS = 100_000
_MAX_INTERFACES = 64
_SHB = b"\x0a\x0d\x0d\x0a"
_BOM = b"\x1a\x2b\x3c\x4d"
_BOM_REVERSED = b"\x4d\x3c\x2b\x1a"


class PcapNgFormatError(ValueError):
    """Raised when PCAPNG input is malformed or exceeds parser limits."""


@dataclass(frozen=True)
class PcapNgPacket:
    index: int
    timestamp: float
    link_type: int
    captured_length: int
    original_length: int
    data: bytes


@dataclass(frozen=True)
class PcapNgCapture:
    packets: tuple[PcapNgPacket, ...]
    source_size: int
    section_count: int
    interface_count: int


@dataclass(frozen=True)
class _Interface:
    link_type: int
    snaplen: int
    timestamp_denominator: int
    timestamp_offset: int


def _options(data: bytes, order: str, *, max_options: int = 128) -> list[tuple[int, bytes]]:
    result: list[tuple[int, bytes]] = []
    offset = 0
    while offset < len(data):
        if len(data) - offset < 4:
            raise PcapNgFormatError("truncated option header")
        code, length = struct.unpack_from(order + "HH", data, offset)
        offset += 4
        if code == 0:
            if length != 0 or any(data[offset:]):
                raise PcapNgFormatError("invalid end-of-options marker")
            return result
        padded = (length + 3) & ~3
        if padded > len(data) - offset:
            raise PcapNgFormatError("option value exceeds block boundary")
        if len(result) >= max_options:
            raise PcapNgFormatError("option count exceeds limit")
        result.append((code, data[offset:offset + length]))
        offset += padded
    return result


def parse_pcapng(
    payload: bytes,
    *,
    max_file_bytes: int = _MAX_FILE_BYTES,
    max_block_bytes: int = _MAX_BLOCK_BYTES,
    max_packets: int = _MAX_PACKETS,
    max_interfaces: int = _MAX_INTERFACES,
) -> PcapNgCapture:
    """Parse Section Header, Interface Description, and Enhanced Packet blocks.

    Unknown block types are skipped only after validating their framing. Simple
    Packet Blocks are rejected because they lack timestamps in this reader's
    current evidence model. The caller owns file I/O; this function accepts bytes.
    """
    if not isinstance(payload, bytes):
        raise PcapNgFormatError("PCAPNG input must be immutable bytes")
    for name, value, upper in (
        ("max_file_bytes", max_file_bytes, _MAX_FILE_BYTES),
        ("max_block_bytes", max_block_bytes, _MAX_BLOCK_BYTES),
        ("max_packets", max_packets, _MAX_PACKETS),
        ("max_interfaces", max_interfaces, _MAX_INTERFACES),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= upper:
            raise PcapNgFormatError(f"{name} is outside supported bounds")
    if len(payload) > max_file_bytes:
        raise PcapNgFormatError("PCAPNG file exceeds size limit")

    offset = 0
    order: str | None = None
    interfaces: list[_Interface] = []
    packets: list[PcapNgPacket] = []
    sections = 0
    total_interfaces = 0
    section_end: int | None = None

    while offset < len(payload):
        if len(payload) - offset < 12:
            raise PcapNgFormatError("truncated block header")
        block_type_bytes = payload[offset:offset + 4]
        if block_type_bytes == _SHB:
            if section_end is not None and offset != section_end:
                raise PcapNgFormatError("section length does not match the next section boundary")
            if len(payload) - offset < 28:
                raise PcapNgFormatError("truncated section header block")
            bom = payload[offset + 8:offset + 12]
            if bom == _BOM:
                order = ">"
            elif bom == _BOM_REVERSED:
                order = "<"
            else:
                raise PcapNgFormatError("invalid section byte-order magic")
        elif order is None:
            raise PcapNgFormatError("section header must be the first block")

        block_type = struct.unpack(order + "I", block_type_bytes)[0]
        block_length = struct.unpack_from(order + "I", payload, offset + 4)[0]
        if block_length < 12 or block_length % 4 or block_length > max_block_bytes:
            raise PcapNgFormatError("invalid block length")
        if block_length > len(payload) - offset:
            raise PcapNgFormatError("truncated block body")
        block = payload[offset:offset + block_length]
        trailing_length = struct.unpack_from(order + "I", block, block_length - 4)[0]
        if trailing_length != block_length:
            raise PcapNgFormatError("block length trailer mismatch")
        body = block[8:-4]

        if block_type_bytes == _SHB:
            major, minor = struct.unpack_from(order + "HH", body, 4)
            if (major, minor) != (1, 0):
                raise PcapNgFormatError("unsupported PCAPNG section version")
            declared_section_length = struct.unpack_from(order + "q", body, 8)[0]
            if declared_section_length < -1:
                raise PcapNgFormatError("invalid negative section length")
            section_end = None if declared_section_length == -1 else offset + block_length + declared_section_length
            if section_end is not None and section_end > len(payload):
                raise PcapNgFormatError("declared section length exceeds input")
            interfaces = []
            sections += 1
            _options(body[16:], order)
        elif block_type == 1:  # Interface Description Block
            if len(body) < 8:
                raise PcapNgFormatError("truncated interface description")
            link_type, reserved, snaplen = struct.unpack_from(order + "HHI", body, 0)
            if reserved != 0 or snaplen == 0 or snaplen > _MAX_BLOCK_BYTES:
                raise PcapNgFormatError("invalid interface metadata")
            denominator = 1_000_000
            timestamp_offset = 0
            for code, value in _options(body[8:], order):
                if code == 9:
                    if len(value) != 1:
                        raise PcapNgFormatError("invalid timestamp resolution option")
                    raw = value[0]
                    exponent = raw & 0x7F
                    denominator = (2 if raw & 0x80 else 10) ** exponent
                    if denominator > 10**18:
                        raise PcapNgFormatError("timestamp resolution is too fine")
                elif code == 14:
                    if len(value) != 8:
                        raise PcapNgFormatError("invalid timestamp offset option")
                    timestamp_offset = struct.unpack(order + "q", value)[0]
            if len(interfaces) >= max_interfaces or total_interfaces >= max_interfaces:
                raise PcapNgFormatError("interface count exceeds limit")
            interfaces.append(_Interface(link_type, snaplen, denominator, timestamp_offset))
            total_interfaces += 1
        elif block_type == 6:  # Enhanced Packet Block
            if len(body) < 20:
                raise PcapNgFormatError("truncated enhanced packet block")
            interface_id, high, low, captured, original = struct.unpack_from(order + "IIIII", body, 0)
            if interface_id >= len(interfaces):
                raise PcapNgFormatError("packet references an unknown interface")
            interface = interfaces[interface_id]
            if captured > interface.snaplen or captured > max_block_bytes or original < captured:
                raise PcapNgFormatError("invalid enhanced packet lengths")
            padded = (captured + 3) & ~3
            if 20 + padded > len(body):
                raise PcapNgFormatError("packet data exceeds block boundary")
            if any(body[20 + captured:20 + padded]):
                raise PcapNgFormatError("non-zero packet padding")
            _options(body[20 + padded:], order)
            if len(packets) >= max_packets:
                raise PcapNgFormatError("packet count exceeds limit")
            ticks = (high << 32) | low
            timestamp = interface.timestamp_offset + ticks / interface.timestamp_denominator
            packets.append(PcapNgPacket(len(packets), timestamp, interface.link_type,
                                        captured, original, body[20:20 + captured]))
        elif block_type == 3:
            raise PcapNgFormatError("Simple Packet Blocks are not supported without timestamps")

        offset += block_length

    if sections == 0:
        raise PcapNgFormatError("PCAPNG contains no section")
    if section_end is not None and offset != section_end:
        raise PcapNgFormatError("section length does not match input boundary")
    return PcapNgCapture(tuple(packets), len(payload), sections, total_interfaces)
