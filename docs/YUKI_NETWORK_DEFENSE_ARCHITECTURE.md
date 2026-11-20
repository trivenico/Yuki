# Yuki Network Defense and Packet Analysis

## Purpose

Build a defensive, owner-controlled network observability workspace that combines useful ideas from Wireshark (protocol analysis and packet timelines), Bettercap (network visibility and asset discovery), and Ettercap (network-path and protocol-risk awareness) without reproducing offensive interception workflows.

This is a product architecture and safety boundary, not a claim that these capabilities already exist.

## Supported defensive capabilities

1. **Offline packet analysis** — import a user-selected PCAP/PCAPNG file, parse bounded packet records, identify common Ethernet, ARP, IPv4, IPv6, TCP, UDP, DNS, and TLS metadata, and render protocol summaries.
2. **Passive local network inventory** — inspect explicitly authorized local interface and route metadata, neighbor-cache snapshots, and socket tables. Do not scan arbitrary address ranges.
3. **Traffic observability** — optional capture from a specifically selected local interface, only after explicit owner action, with a visible capture indicator, duration/size limits, and a stop control.
4. **Protocol anomaly detection** — explain malformed headers, suspicious DNS patterns, unexpected listeners, unusual protocol combinations, and cleartext protocol exposure. Findings are hypotheses with packet/frame references, not verdicts.
5. **Network topology view** — visualize only observed devices, routes, and connections; do not infer ownership or identity from an IP address alone.
6. **Evidence export** — redact payloads and sensitive identifiers by default; preserve hashes, timestamps, parser version, and source provenance for user-requested reports.

## Explicitly excluded

- ARP/DNS spoofing, poisoning, MITM interception, credential harvesting, session hijacking, deauthentication, stealth, persistence, exploit delivery, or bypassing network access controls.
- Capturing traffic on interfaces or networks without explicit authorization.
- Active scanning of public or third-party address ranges.
- Injecting, modifying, replaying, or redirecting packets.
- Automatically changing firewall, routing, DNS, proxy, or interface configuration.

## Permission and privacy model

- Default mode is offline analysis of files the owner explicitly selects.
- Live capture is off by default and requires an explicit, per-session owner action naming the local interface and capture limit.
- Capture must be visibly indicated and cancellable; no background or hidden capture.
- No capture of Wi-Fi monitor mode or promiscuous traffic by default; any future support requires a separate reviewed policy and clear authorization boundary.
- Payload capture is minimized; metadata-only mode is the default. Secrets, cookies, authorization headers, and personal data must be redacted from logs and exports.
- Every import, capture start/stop, export, and configuration change is recorded in a local audit trail.
- Network tools run without elevated privileges where possible. No raw-socket or packet-injection permission is granted to specialist agents.
- Analysis results are untrusted observations. They cannot authorize firewall changes, execution, or other actions.

## Architecture

- `defense/network/`: domain models, bounded parsers, protocol decoders, redaction, evidence provenance.
- `defense/network/pcap/`: offline PCAP/PCAPNG readers and format validation.
- `defense/network/capture/`: optional owner-triggered capture adapter; disabled until platform-specific permission and resource controls are reviewed.
- `defense/network/detection/`: deterministic, explainable rules with false-positive notes.
- `ui/network/`: packet list, packet detail, filters, topology, capture status and audit history.
- Integration with the existing specialist coordinator is read-only initially. Network findings may be summarized but cannot dispatch privileged actions.

## Resource and parser safety

- Strict bounds on file size, packet count, per-packet length, nesting depth, protocol extension chains, string lengths, and total parse time.
- Reject malformed lengths, integer overflows, truncated records, invalid offsets, unsupported link types, and ambiguous encodings.
- No automatic decompression, external lookups, DNS resolution, or payload execution during parsing.
- PCAP/PCAPNG imports are treated as hostile input and fuzz-tested.
- Avoid retaining raw payloads unless the owner explicitly opts in; apply redaction before logs or exports.

## Delivery sequence

1. Complete read-only Linux route and socket observations (IPv4/IPv6, TCP/UDP, Unix sockets).
2. Build offline classic-PCAP reader with strict bounds and tests.
3. Add Ethernet, ARP, IPv4, IPv6, TCP, UDP, DNS metadata dissectors.
4. Add bounded PCAPNG support after classic-PCAP parser hardening.
5. Add explainable anomaly rules and evidence-linked reports.
6. Design an owner-controlled, visible live-capture adapter only after privacy, privilege, and resource reviews.
7. Add UI and read-only specialist integration.

## Release gate

No live capture ships until explicit consent UX, visible status, stop behavior, permission minimization, bounded storage, redaction, audit records, and platform-specific security review are implemented. No offensive interception or packet manipulation feature is in scope.


## Offline packet metadata dissector

The classic-PCAP foundation now has a pure byte-oriented metadata dissector for Ethernet, up to two VLAN tags, ARP, IPv4, IPv6 base headers, TCP, and UDP. It reports bounded addresses, ports, header metadata, and payload lengths only; it never returns payload bytes. Non-initial IPv4 fragments and IPv6 extension/ESP chains are marked undecoded rather than guessed. Unsupported link types remain metadata-only. DNS/TLS application metadata, PCAPNG, and evidence correlation remain future work. The dissector does not open files, capture traffic, resolve names, or contact remote systems.


## Offline DNS metadata

The offline packet-analysis layer now includes a bounded DNS message parser for questions and resource records. It validates section counts, label encoding, compression pointers and loops, record lengths, and trailing bytes. A/AAAA records are normalized to addresses; CNAME/NS/PTR values are decoded as names; TXT contents are never retained (only a segment count and byte count are reported). It performs no DNS resolution or network access. The shared offline capture pipeline now invokes this parser for UDP traffic involving port 53. DNS-over-TCP, message correlation, DNS-over-TLS/HTTPS, and broader application-layer decoding remain future work.


## Offline TLS ClientHello metadata

A bounded TLS record/ClientHello parser now extracts legacy and supported protocol versions, cipher-suite identifiers, SNI hostname, ALPN identifiers, session-ID length, and compression methods. It validates record, handshake, vector, and extension lengths; rejects duplicate extensions and malformed SNI/ALPN values; and does not retain raw handshake bytes. SNI is potentially identifying metadata and must be redacted in default exports/logs. The parser is now invoked by the shared offline capture pipeline after bounded TCP reassembly. The current path handles a ClientHello contained in one TLS handshake record; multi-record handshake assembly, TLS decryption, and application-data inspection remain unsupported.


## Offline TCP stream reassembly

A reusable bounded TCP stream reassembler accepts immutable segment payload bytes and an explicit four-tuple flow key. The caller initializes each flow with its expected payload sequence (normally SYN sequence + 1). It handles modular sequence wraparound, out-of-order buffering, contiguous delivery, duplicate retransmissions, idle expiry, and strict per-flow/global memory and segment-count limits. Ambiguous overlaps and partial overlap with already delivered bytes are rejected rather than guessed. The packet dissector now exposes TCP sequence numbers and flags, and the shared classic-PCAP/PCAPNG pipeline seeds flows from observed SYN packets and feeds the assembler. It performs no capture, socket I/O, or network transmission.


## Integrated offline capture analysis

`analyze_capture` composes the classic-PCAP packet records, metadata dissector, DNS parser, bounded TCP reassembler, and TLS ClientHello parser. UDP payloads are offered to DNS parsing only when either port is 53. TCP flows are admitted only after an observed SYN without ACK establishes the initial sequence; directions are tracked independently. The pipeline returns packet metadata, bounded DNS/TLS summaries, and rejection notes, not raw application payload bytes. It has explicit packet, flow, and buffer bounds. Limitations: the current TLS path recognizes a ClientHello contained in one TLS handshake record after TCP reassembly; it does not support multi-record handshake assembly, TLS decryption, QUIC, or encrypted DNS. SNI remains sensitive metadata and must be redacted in exports. PCAPNG support remains outstanding.


## Offline PCAPNG support

The offline reader supports Section Header Blocks, Interface Description Blocks, and Enhanced Packet Blocks. It validates block framing and trailing lengths, declared section boundaries, section byte order, version, interface references, captured/original lengths, packet padding, option boundaries, timestamp resolution and timestamp offsets. Per-interface link types and timestamps are preserved in immutable packet records, and the shared offline analysis pipeline accepts both classic PCAP and PCAPNG captures. Unknown block types are skipped only after framing validation; Simple Packet Blocks are rejected because the current evidence model requires timestamps. This is a bounded reader for the supported subset, not full PCAPNG feature coverage. Statistics blocks, name-resolution blocks, decryption secrets, and other packet block variants are not interpreted.
