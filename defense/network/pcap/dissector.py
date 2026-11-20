"""Bounded metadata-only Ethernet/IP transport dissector for offline packet bytes."""
from __future__ import annotations

import ipaddress
import struct
from dataclasses import dataclass


class DissectionError(ValueError):
    """Raised for invalid dissector inputs or unsupported bounds."""


@dataclass(frozen=True)
class PacketMetadata:
    link_type: int
    network_protocol: str
    transport_protocol: str | None
    source: str | None
    destination: str | None
    source_port: int | None
    destination_port: int | None
    payload_length: int
    details: tuple[str, ...] = ()


def _mac(raw: bytes) -> str:
    return ":".join(f"{part:02x}" for part in raw)


def dissect_packet(data: bytes, *, link_type: int, max_packet_bytes: int = 262_144) -> PacketMetadata:
    """Decode bounded headers only; never returns or executes packet payload bytes."""
    if not isinstance(data, bytes) or not 0 <= len(data) <= max_packet_bytes:
        raise DissectionError("packet must be immutable bytes within the configured bound")
    if isinstance(link_type, bool) or not isinstance(link_type, int) or not 0 <= link_type <= 0xFFFF:
        raise DissectionError("invalid link type")

    offset = 0
    ethertype = None
    details: list[str] = []
    if link_type == 1:  # DLT_EN10MB
        if len(data) < 14:
            raise DissectionError("truncated Ethernet header")
        details.extend((f"src_mac={_mac(data[6:12])}", f"dst_mac={_mac(data[0:6])}"))
        ethertype = struct.unpack_from("!H", data, 12)[0]
        offset = 14
        vlan_count = 0
        while ethertype in (0x8100, 0x88A8, 0x9100):
            if vlan_count >= 2 or len(data) < offset + 4:
                raise DissectionError("truncated or excessive VLAN headers")
            tci, ethertype = struct.unpack_from("!HH", data, offset)
            details.append(f"vlan_id={tci & 0x0FFF}")
            offset += 4
            vlan_count += 1
    elif link_type == 101:  # DLT_RAW
        if not data:
            raise DissectionError("empty raw IP packet")
        version = data[0] >> 4
        ethertype = {4: 0x0800, 6: 0x86DD}.get(version)
        if ethertype is None:
            raise DissectionError("unsupported raw IP version")
    else:
        return PacketMetadata(link_type, "unsupported_link_type", None, None, None, None, None, len(data))

    if ethertype == 0x0806:
        if len(data) < offset + 28:
            raise DissectionError("truncated ARP header")
        htype, ptype, hlen, plen, operation = struct.unpack_from("!HHBBH", data, offset)
        if hlen != 6 or plen != 4 or htype != 1 or ptype != 0x0800:
            return PacketMetadata(link_type, "arp", None, None, None, None, None, len(data) - offset,
                                  tuple(details + ["unsupported_arp_address_format"]))
        base = offset + 8
        src_ip = str(ipaddress.IPv4Address(data[base + 6:base + 10]))
        dst_ip = str(ipaddress.IPv4Address(data[base + 16:base + 20]))
        details.append(f"arp_operation={operation}")
        details.append(f"src_mac={_mac(data[base:base + 6])}")
        details.append(f"dst_mac={_mac(data[base + 10:base + 16])}")
        return PacketMetadata(link_type, "arp", None, src_ip, dst_ip, None, None,
                              len(data) - (offset + 28), tuple(details))
    if ethertype == 0x0800:
        if len(data) < offset + 20:
            raise DissectionError("truncated IPv4 header")
        version_ihl = data[offset]
        version, ihl = version_ihl >> 4, (version_ihl & 0x0F) * 4
        if version != 4 or ihl < 20 or len(data) < offset + ihl:
            raise DissectionError("invalid IPv4 header length or version")
        total_length = struct.unpack_from("!H", data, offset + 2)[0]
        if total_length < ihl or total_length > len(data) - offset:
            raise DissectionError("invalid or truncated IPv4 total length")
        proto = data[offset + 9]
        source = str(ipaddress.IPv4Address(data[offset + 12:offset + 16]))
        destination = str(ipaddress.IPv4Address(data[offset + 16:offset + 20]))
        fragment = struct.unpack_from("!H", data, offset + 6)[0]
        if fragment & 0x1FFF:
            return PacketMetadata(link_type, "ipv4", None, source, destination, None, None,
                                  total_length - ihl, tuple(details + ["non_initial_fragment"]))
        transport_offset = offset + ihl
        end = offset + total_length
        network = "ipv4"
    elif ethertype == 0x86DD:
        if len(data) < offset + 40 or data[offset] >> 4 != 6:
            raise DissectionError("truncated or invalid IPv6 header")
        payload_length = struct.unpack_from("!H", data, offset + 4)[0]
        if payload_length > len(data) - offset - 40:
            raise DissectionError("truncated IPv6 payload")
        proto = data[offset + 6]
        source = str(ipaddress.IPv6Address(data[offset + 8:offset + 24]))
        destination = str(ipaddress.IPv6Address(data[offset + 24:offset + 40]))
        transport_offset = offset + 40
        end = transport_offset + payload_length
        network = "ipv6"
        if proto in (0, 43, 44, 50, 51, 60):
            return PacketMetadata(link_type, network, None, source, destination, None, None,
                                  payload_length, tuple(details + ["ipv6_extension_or_esp_not_decoded"]))
    else:
        return PacketMetadata(link_type, "other", None, None, None, None, None,
                              len(data) - offset, tuple(details))

    source_port = destination_port = None
    transport = None
    if proto == 6:
        if end - transport_offset < 20:
            raise DissectionError("truncated TCP header")
        source_port, destination_port = struct.unpack_from("!HH", data, transport_offset)
        header_len = (data[transport_offset + 12] >> 4) * 4
        if header_len < 20 or header_len > end - transport_offset:
            raise DissectionError("invalid TCP data offset")
        transport = "tcp"
        payload_length = end - transport_offset - header_len
    elif proto == 17:
        if end - transport_offset < 8:
            raise DissectionError("truncated UDP header")
        source_port, destination_port, udp_len = struct.unpack_from("!HHH", data, transport_offset)
        if udp_len < 8 or udp_len > end - transport_offset:
            raise DissectionError("invalid UDP length")
        transport = "udp"
        payload_length = udp_len - 8
    else:
        payload_length = end - transport_offset
    return PacketMetadata(link_type, network, transport, source, destination,
                          source_port, destination_port, payload_length, tuple(details))
