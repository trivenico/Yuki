# Rust PCAP Guard

Yuki's offline packet-analysis pipeline now has an optional Rust validation
accelerator for classic PCAP framing. The Python parser remains the stable
public API and fallback; Rust does not own packet analysis, evidence policy,
file access, or network access.

## Build the optional extension

From the repository root:

```bash
python -m pip install maturin
maturin develop --manifest-path native/pcap_guard/Cargo.toml
```

For a release wheel:

```bash
maturin build --manifest-path native/pcap_guard/Cargo.toml --release
```

If the extension is not installed, `defense.network.pcap.reader.parse_pcap`
uses its existing pure-Python implementation. Both paths return the same
`PcapCapture` / `PcapPacket` dataclasses and raise `PcapFormatError` for
malformed input.

## Security boundary

- Accepts immutable in-memory bytes only.
- Does not open files, capture packets, transmit packets, or invoke system tools.
- Enforces bounded input size, packet size, packet count, timestamp fractions,
  snaplen and record boundaries.
- Does not interpret packet payloads; existing Python dissection and evidence
  handling remain authoritative.
- Does not make authorization decisions or enable offensive network activity.

The dedicated `Yuki Rust PCAP guard` workflow runs Cargo tests, Clippy, builds
the optional Python extension and checks Rust/Python parser contract parity.
The Rust accelerator should not become the default for release builds until
cross-platform CI and representative benchmarks demonstrate parity and a
measurable performance benefit.
