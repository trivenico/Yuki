# Yuki Security Tool Integration Matrix

## Purpose

Yuki will provide a unified defensive security workspace inspired by established network, web, API, wireless, and password-audit tools. This matrix describes intended integrations and safe product boundaries; it does not claim that third-party tools are installed or that the listed capabilities are implemented.

## Integration principles

- Prefer native, bounded parsers and read-only analyzers before wrapping external executables.
- External tools are optional adapters, never trusted agents. Pin versions, verify provenance, use explicit executable paths, scrub environment variables, bound output/time/resources, and run only in a separately verified isolated backend.
- Offline file analysis is the default. Any interaction with a live target requires an explicit scope, owner confirmation, authorization record, rate/resource limits, and an auditable session.
- No credentials, tokens, cookies, private keys, or raw sensitive payloads in prompts, logs, reports, or telemetry.
- Findings are evidence-linked hypotheses with confidence and limitations, not automatic proof of compromise.
- No tool may independently grant permissions, approve plans, execute arbitrary commands, or alter host/network configuration.

## Tool-family mapping

| Reference tool | Yuki capability | Initial safe scope | Gated or excluded scope |
|---|---|---|---|
| Nmap | Network asset and service inventory | Import existing scan XML/JSON; validate owner-provided local inventory; passive route/socket observations | Active probes only against an explicitly authorized allow-listed lab or asset scope; no public-range scanning |
| Metasploit | Vulnerability validation knowledge | Parse/import reports; map findings to remediation and safe local reproductions | No exploit delivery, payload generation, persistence, post-exploitation, or remote shells |
| mitmproxy | HTTP traffic inspection | Offline HAR/flow import, header/cookie redaction, TLS metadata and policy analysis | No interception of third-party traffic, certificate impersonation, credential/session capture, or transparent proxying |
| Burp Suite | Web application security review | Import proxy history/scanner reports; passive request/response linting and evidence tracking | Active scanner/fuzzer only in an authorized test environment with explicit scope and rate limits; no auth bypass or session theft |
| OWASP ZAP | Web application baseline assessment | Import reports; passive rules; local intentionally vulnerable fixtures | Active spider/scan only for explicitly authorized targets; destructive rules disabled |
| Caido | HTTP investigation workspace | Import sanitized HTTP sessions and findings; request/response organization | No hidden interception or unauthorized traffic collection |
| sqlmap | SQL injection risk review | Static query review, safe fixture tests, imported finding triage, remediation guidance | No automated exploitation, data extraction, file read/write, OS command execution, or third-party probing |
| John the Ripper | Password storage audit | Offline hash-format identification and policy assessment; synthetic fixture validation | No cracking of real-user credentials or acquired hashes without a separate explicit, lawful audit policy; no credential use |
| Aircrack-ng | Wireless security posture | Import and analyze owner-provided wireless audit metadata; configuration guidance | No deauthentication, handshake capture against others, key cracking, rogue AP, injection, or monitor-mode collection by default |
| Hydra | Authentication resilience | Review rate-limit/lockout configuration and test synthetic local fixtures | No password spraying, brute force, credential stuffing, or login attempts against real services |
| Netcat | Connectivity diagnostics | Local loopback diagnostics and parsing of owner-supplied transcripts | No reverse shells, bind shells, arbitrary remote command channels, or unauthenticated listeners |
| Hoppscotch | API development and testing | OpenAPI/Postman collection import, schema validation, local mock requests, response assertions | Live requests only to explicitly allow-listed APIs with safe methods by default; no secret exfiltration or destructive calls |
| Wireshark | Packet analysis | Offline PCAP/PCAPNG parsing and protocol metadata | Live capture separately gated; no packet injection or modification |
| Bettercap / Ettercap | Network visibility | Passive interface/route/neighbor/socket observations and evidence-linked topology | No spoofing, poisoning, MITM, deauth, traffic redirection, or packet manipulation |
| Nuclei | Template-based vulnerability assessment | Offline import of bounded JSONL findings, URL redaction, evidence provenance, and remediation triage | Template execution, live scans, OAST callbacks, code/headless/workflow templates, fuzzing, and destructive checks remain disabled until provenance, scope, and verified isolation gates pass |
| SpiderFoot / OSINT Framework | Passive asset intelligence | Import owner-supplied domain, IP, URL, ASN, and SHA-256 observations into a provenance-aware graph; normalize indicators and merge evidence | No people-search, doxxing, credential/leak harvesting, deanonymization, unrestricted scraping, or automatic third-party enrichment |
| Maltego | Link analysis and graph exploration | Visualize relationships between technical asset indicators with evidence references and confidence limitations | No identity profiling, sensitive-person inference, or unsupported attribution; graph edges are observations, not proof |
| PRISM (reference ambiguous) | Not assigned pending identification | No implementation claimed until the exact product/project is identified | No mass surveillance, covert collection, interception, or personal-data aggregation |

## Delivery order

1. **Offline foundation:** classic PCAP reader, then PCAPNG; strict bounds, malformed-input tests, provenance and redaction.
2. **Protocol analysis:** Ethernet, ARP, IPv4/IPv6, TCP/UDP, DNS and TLS metadata; no payload execution or external lookups.
3. **Web/API evidence:** sanitized HAR, OpenAPI, scanner-report import (including Nuclei JSONL), passive checks and remediation.
4. **Asset intelligence:** offline indicator normalization, provenance-aware OSINT graph import, source evidence and URL redaction.
5. **Inventory and posture:** passive local observations and imported scan results; no arbitrary target discovery.
6. **Isolated lab validation:** only after a real, independently verified sandbox backend, scope authorization, cancellation, quotas and audit.
7. **External adapters:** one tool at a time, with pinned versions, signed/provenance-checked artifacts, allow-listed command schemas, and tests using disposable fixtures.
8. **Owner-visible UX:** scope review, approval prompt, session indicator, stop control, evidence timeline and export.

## Current implementation status

- The Linux process network observer provides diagnostic-only local interface counters, routes, and socket-table observations.
- The classic-PCAP parser accepts immutable bytes only, supports four classic-PCAP byte-order/timestamp magic variants, preserves packet bytes and timestamp components, and enforces file, packet, snaplen, and packet-count bounds.
- The parser does not read paths, capture traffic, decode protocols, contact remote systems, or execute payloads.
- The Nuclei JSONL importer retains bounded finding metadata only; it drops raw request/response and extracted-result values, strips URL credentials/query/fragment data, and labels findings unverified. It never executes templates or contacts targets.
- The offline PCAP/PCAPNG readers, metadata dissectors, DNS/TLS parsing, DNS transaction correlation, and passive OSINT asset graph importer are implemented as bounded foundations. The interactive graph UI, broader report adapters, external tool adapters, live capture, and UI remain outstanding.
- The existing regression-test execution adapter still rejects the host runner; no verified sandbox backend currently enables untrusted tool execution.

## Release gates for active testing

A live assessment cannot start unless all of the following are true:

1. The owner has recorded authorization and exact target scope, including exclusions.
2. The selected operation is on a reviewed allow-list and cannot expand scope.
3. A trusted isolated backend has independently verified its effective controls; self-reported Docker metadata is insufficient.
4. Secrets are scoped and injected only when required, never exposed to model context or logs.
5. Rate, time, concurrency, memory, output, and storage limits are enforced.
6. Cancellation is immediate and tested; all activity is visible and audited.
7. The run produces evidence and remediation guidance, not automatic changes.
8. Destructive, persistence, stealth, credential theft, packet manipulation, and post-exploitation actions remain unavailable.


## Passive OSINT asset graph foundation

Yuki includes an offline JSONL importer for owner-supplied technical asset observations. It normalizes domain names, IP addresses, HTTP(S) URLs, ASNs, and SHA-256 fingerprints; URL credentials, query strings, and fragments are discarded. It builds deterministic nodes and relationship edges and attaches evidence identifiers to both observations and edges. All source records remain explicitly unverified. The importer performs no DNS resolution, HTTP requests, scraping, third-party enrichment, people search, identity resolution, or attribution. Inputs are bounded by file size, record count, line length, and supported field/type allow-lists. Malformed or unknown fields are rejected. The PRISM reference remains unimplemented until the intended product is identified.
