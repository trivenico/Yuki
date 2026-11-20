"""Capture-level offline analysis that joins packet, DNS, TCP, and TLS metadata."""
from __future__ import annotations

from dataclasses import dataclass

from defense.network.pcap.dissector import DissectionError, PacketMetadata, dissect_packet
from defense.network.pcap.dns import DnsFormatError, DnsMessage, parse_dns_message
from defense.network.pcap.dns_correlation import DnsCorrelation, correlate_dns_messages
from defense.network.pcap.reader import PcapCapture
from defense.network.pcap.pcapng import PcapNgCapture
from defense.network.pcap.tcp_reassembly import TcpReassemblyError, TcpStreamReassembler
from defense.network.pcap.tls import TlsClientHello, TlsFormatError, parse_tls_client_hello


@dataclass(frozen=True)
class AnalyzedPacket:
    index: int
    timestamp: float
    metadata: PacketMetadata
    dns: DnsMessage | None = None
    tls_client_hello: TlsClientHello | None = None
    analysis_notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class CaptureAnalysis:
    packets: tuple[AnalyzedPacket, ...]
    packet_count: int
    decoded_count: int
    dns_count: int
    tls_client_hello_count: int
    analysis_errors: tuple[str, ...]
    dns_correlation: DnsCorrelation


def analyze_capture(
    capture: PcapCapture | PcapNgCapture,
    *,
    max_analyzed_packets: int = 50_000,
    max_tcp_flows: int = 128,
    max_tcp_buffer_bytes: int = 4 * 1024 * 1024,
) -> CaptureAnalysis:
    """Analyze an already-parsed capture; never opens files or accesses a network.

    Only UDP packets using port 53 are offered to the DNS parser. TCP streams
    are started only on observed SYN packets and reassembled independently by
    direction. Payload bytes remain local to this function and are not returned.
    """
    if not isinstance(capture, (PcapCapture, PcapNgCapture)):
        raise TypeError("capture must be a parsed PCAP or PCAPNG capture")
    if (isinstance(max_analyzed_packets, bool) or not isinstance(max_analyzed_packets, int)
        or not 1 <= max_analyzed_packets <= 100_000):
        raise ValueError("max_analyzed_packets is outside supported bounds")
    if len(capture.packets) > max_analyzed_packets:
        raise ValueError("capture exceeds the analysis packet-count limit")
    reassembler = TcpStreamReassembler(
        max_flows=max_tcp_flows,
        max_buffered_bytes=max_tcp_buffer_bytes,
        max_flow_bytes=min(256 * 1024, max_tcp_buffer_bytes),
        max_segment_bytes=min(65_535, max_tcp_buffer_bytes),
    )
    active_syn: set[tuple[str, int, str, int]] = set()
    stream_prefixes: dict[tuple[str, int, str, int], bytearray] = {}
    tls_checked: set[tuple[str, int, str, int]] = set()
    analyzed: list[AnalyzedPacket] = []
    errors: list[str] = []
    decoded = dns_count = tls_count = 0

    for packet in capture.packets:
        if isinstance(capture, PcapNgCapture):
            timestamp = packet.timestamp
            link_type = packet.link_type
        else:
            timestamp = packet.timestamp_seconds + packet.timestamp_fraction / capture.timestamp_resolution
            link_type = capture.link_type
        try:
            meta = dissect_packet(packet.data, link_type=link_type)
        except DissectionError as exc:
            errors.append(f"packet {packet.index}: dissection rejected ({exc})")
            continue
        decoded += 1
        dns = None
        tls = None
        notes: list[str] = []
        if meta.transport_protocol == "udp" and 53 in (meta.source_port, meta.destination_port):
            if meta.payload_offset is not None:
                payload = packet.data[meta.payload_offset:meta.payload_offset + meta.payload_length]
                try:
                    dns = parse_dns_message(payload, max_message_bytes=4096)
                    dns_count += 1
                except DnsFormatError:
                    notes.append("dns_payload_not_decoded")
        elif meta.transport_protocol == "tcp" and meta.source and meta.destination:
            flags = meta.tcp_flags or 0
            key = (meta.source, meta.source_port or 0, meta.destination, meta.destination_port or 0)
            syn = bool(flags & 0x02)
            fin_or_rst = bool(flags & (0x01 | 0x04))
            if syn and not (flags & 0x10):
                active_syn.add(key)
                try:
                    reassembler.start(key, ((meta.tcp_sequence or 0) + 1) & 0xFFFFFFFF, timestamp=timestamp)
                except TcpReassemblyError as exc:
                    active_syn.discard(key)
                    notes.append("tcp_flow_start_rejected")
            if key in active_syn and meta.payload_offset is not None and meta.payload_length:
                payload = packet.data[meta.payload_offset:meta.payload_offset + meta.payload_length]
                sequence = (meta.tcp_sequence or 0) + (1 if syn else 0)
                try:
                    assembled = reassembler.feed(
                        key, sequence & 0xFFFFFFFF, payload,
                        initial_sequence=sequence & 0xFFFFFFFF if syn else None,
                        timestamp=timestamp,
                    )
                    if assembled.data and key not in tls_checked:
                        prefix = stream_prefixes.setdefault(key, bytearray())
                        if len(prefix) + len(assembled.data) > 18_437:
                            notes.append("tls_record_exceeds_inspection_bound")
                            stream_prefixes.pop(key, None)
                            tls_checked.add(key)
                        else:
                            prefix.extend(assembled.data)
                            if prefix and prefix[0] != 22:
                                stream_prefixes.pop(key, None)
                                tls_checked.add(key)
                            elif len(prefix) >= 5:
                                total_length = 5 + int.from_bytes(prefix[3:5], "big")
                                if total_length > 18_437:
                                    notes.append("tls_record_exceeds_inspection_bound")
                                    stream_prefixes.pop(key, None)
                                    tls_checked.add(key)
                                elif len(prefix) >= total_length:
                                    try:
                                        tls = parse_tls_client_hello(bytes(prefix[:total_length]))
                                        tls_count += 1
                                    except TlsFormatError:
                                        notes.append("tls_handshake_not_client_hello_or_invalid")
                                    stream_prefixes.pop(key, None)
                                    tls_checked.add(key)
                except TcpReassemblyError as exc:
                    notes.append(f"tcp_reassembly_rejected: {exc}")
                    errors.append(f"packet {packet.index}: TCP stream rejected ({exc})")
                    active_syn.discard(key)
            if fin_or_rst:
                active_syn.discard(key)
                stream_prefixes.pop(key, None)
                tls_checked.discard(key)
        analyzed.append(AnalyzedPacket(packet.index, timestamp, meta, dns, tls, tuple(notes)))

    return CaptureAnalysis(tuple(analyzed), len(capture.packets), decoded, dns_count, tls_count, tuple(errors),
                          correlate_dns_messages(analyzed))
