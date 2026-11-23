"""Compare the optional Rust PCAP parser with the Python reference parser.

Run with:
    python benchmarks/bench_pcap_guard.py --iterations 1000

This benchmark is informational: it has no pass/fail speed threshold and never
changes which parser is selected. It uses a deterministic in-memory capture.
"""
from __future__ import annotations

import argparse
import statistics
import struct
import time
from unittest.mock import patch

from defense.network.pcap import reader


def fixture(packet_count: int = 256, packet_size: int = 512) -> bytes:
    if not 1 <= packet_count <= reader._MAX_PACKETS:
        raise ValueError("packet_count is outside supported bounds")
    if not 1 <= packet_size <= reader._MAX_PACKET_BYTES:
        raise ValueError("packet_size is outside supported bounds")
    header = struct.pack("<IHHIIII", 0xA1B2C3D4, 2, 4, 0, 0, 65535, 1)
    packet = bytes((index % 251 for index in range(packet_size)))
    records = b"".join(
        struct.pack("<IIII", index, 0, packet_size, packet_size) + packet
        for index in range(packet_count)
    )
    return header + records


def measure(callable_, payload: bytes, iterations: int) -> float:
    samples = []
    for _ in range(5):
        started = time.perf_counter()
        for _ in range(iterations):
            callable_(payload)
        samples.append((time.perf_counter() - started) / iterations)
    return statistics.median(samples)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=int, default=100)
    parser.add_argument("--packets", type=int, default=256)
    parser.add_argument("--packet-size", type=int, default=512)
    args = parser.parse_args()
    if type(args.iterations) is not int or not 1 <= args.iterations <= 100_000:
        parser.error("--iterations must be from 1 to 100000")

    payload = fixture(args.packets, args.packet_size)
    native = reader._rust_parse_pcap
    with patch.object(reader, "_rust_parse_pcap", None):
        python_seconds = measure(reader.parse_pcap, payload, args.iterations)

    print(f"capture_bytes={len(payload)} packets={args.packets} iterations={args.iterations}")
    print(f"python_median_seconds={python_seconds:.9f}")
    if native is None:
        print("rust_extension=unavailable (install the optional maturin wheel)")
        return 0

    def rust_parse(data: bytes):
        return native(data, reader._MAX_FILE_BYTES, reader._MAX_PACKET_BYTES, reader._MAX_PACKETS)

    rust_seconds = measure(rust_parse, payload, args.iterations)
    print(f"rust_median_seconds={rust_seconds:.9f}")
    if rust_seconds > 0:
        print(f"python_over_rust_ratio={python_seconds / rust_seconds:.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
