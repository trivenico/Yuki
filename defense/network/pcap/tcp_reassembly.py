"""Bounded, offline TCP sequence reassembly primitives.

The caller supplies segments from an explicitly selected capture. This module
performs no socket I/O, capture, retransmission, or network access.
"""
from __future__ import annotations

from dataclasses import dataclass, field


class TcpReassemblyError(ValueError):
    """Raised for invalid TCP segment metadata or resource-limit violations."""


@dataclass
class _Flow:
    next_sequence: int
    pending: dict[int, bytes] = field(default_factory=dict)
    pending_bytes: int = 0
    last_timestamp: float = 0.0


@dataclass(frozen=True)
class TcpReassemblyResult:
    flow_key: tuple[str, int, str, int]
    data: bytes
    next_sequence: int
    buffered_bytes: int
    duplicate: bool = False


class TcpStreamReassembler:
    """Reassemble contiguous TCP payload bytes with strict caller-controlled bounds.

    A flow must be initialized with its first expected payload sequence number
    (normally SYN sequence + 1). Sequence numbers wrap at 2**32. Overlapping
    pending segments are rejected rather than selecting potentially evasive
    interpretations. Fully retransmitted data behind the delivery cursor is
    ignored as a duplicate.
    """

    def __init__(self, *, max_flows: int = 128, max_buffered_bytes: int = 4 * 1024 * 1024,
                 max_flow_bytes: int = 256 * 1024, max_segment_bytes: int = 65_535,
                 max_pending_segments: int = 256, max_idle_seconds: float = 120.0):
        limits = (max_flows, max_buffered_bytes, max_flow_bytes, max_segment_bytes, max_pending_segments)
        if any(isinstance(v, bool) or not isinstance(v, int) or v < 1 for v in limits):
            raise TcpReassemblyError("integer limits must be positive")
        if max_flows > 4096 or max_buffered_bytes > 64 * 1024 * 1024:
            raise TcpReassemblyError("global reassembly limits exceed hard caps")
        if max_flow_bytes > max_buffered_bytes or max_segment_bytes > max_flow_bytes:
            raise TcpReassemblyError("inconsistent reassembly limits")
        if isinstance(max_idle_seconds, bool) or not isinstance(max_idle_seconds, (int, float)) or max_idle_seconds <= 0:
            raise TcpReassemblyError("idle timeout must be positive")
        self.max_flows = max_flows
        self.max_buffered_bytes = max_buffered_bytes
        self.max_flow_bytes = max_flow_bytes
        self.max_segment_bytes = max_segment_bytes
        self.max_pending_segments = max_pending_segments
        self.max_idle_seconds = float(max_idle_seconds)
        self._flows: dict[tuple[str, int, str, int], _Flow] = {}
        self._buffered_bytes = 0

    @property
    def buffered_bytes(self) -> int:
        return self._buffered_bytes

    @staticmethod
    def _validate_key(key: tuple[str, int, str, int]) -> None:
        if (not isinstance(key, tuple) or len(key) != 4 or
            not isinstance(key[0], str) or not isinstance(key[2], str) or
            not key[0] or not key[2] or
            any(isinstance(p, bool) or not isinstance(p, int) or not 0 <= p <= 65535
                for p in (key[1], key[3]))):
            raise TcpReassemblyError("invalid TCP flow key")

    @staticmethod
    def _distance(sequence: int, reference: int) -> int:
        """Signed modular distance in the unambiguous half of TCP sequence space."""
        return ((sequence - reference + (1 << 31)) & 0xFFFFFFFF) - (1 << 31)

    def expire(self, timestamp: float) -> int:
        """Expire flows idle for the configured duration using caller timestamps."""
        if isinstance(timestamp, bool) or not isinstance(timestamp, (int, float)):
            raise TcpReassemblyError("timestamp must be numeric")
        expired = []
        for key, flow in self._flows.items():
            if timestamp < flow.last_timestamp:
                raise TcpReassemblyError("timestamps must be monotonic per reassembler")
            if timestamp - flow.last_timestamp > self.max_idle_seconds:
                expired.append(key)
        for key in expired:
            self._buffered_bytes -= self._flows[key].pending_bytes
            del self._flows[key]
        return len(expired)

    def feed(self, flow_key: tuple[str, int, str, int], sequence: int, payload: bytes, *,
             initial_sequence: int | None = None, timestamp: float = 0.0) -> TcpReassemblyResult:
        self._validate_key(flow_key)
        if isinstance(sequence, bool) or not isinstance(sequence, int) or not 0 <= sequence <= 0xFFFFFFFF:
            raise TcpReassemblyError("sequence must be an unsigned 32-bit integer")
        if not isinstance(payload, bytes) or len(payload) > self.max_segment_bytes:
            raise TcpReassemblyError("segment payload must be immutable bytes within bounds")
        if isinstance(timestamp, bool) or not isinstance(timestamp, (int, float)):
            raise TcpReassemblyError("timestamp must be numeric")
        if not payload:
            flow = self._flows.get(flow_key)
            return TcpReassemblyResult(flow_key, b"", flow.next_sequence if flow else sequence, self._buffered_bytes)
        self.expire(timestamp)
        flow = self._flows.get(flow_key)
        if flow is None:
            if initial_sequence is None:
                initial_sequence = sequence
            if (isinstance(initial_sequence, bool) or not isinstance(initial_sequence, int)
                or not 0 <= initial_sequence <= 0xFFFFFFFF):
                raise TcpReassemblyError("initial sequence must be an unsigned 32-bit integer")
            if len(self._flows) >= self.max_flows:
                raise TcpReassemblyError("maximum TCP flow count exceeded")
            flow = _Flow(initial_sequence, last_timestamp=float(timestamp))
            self._flows[flow_key] = flow
        elif initial_sequence is not None and initial_sequence != flow.next_sequence and initial_sequence != (flow.next_sequence & 0xFFFFFFFF):
            raise TcpReassemblyError("initial sequence cannot be reset on an active flow")
        if timestamp < flow.last_timestamp:
            raise TcpReassemblyError("timestamps must be monotonic per flow")
        flow.last_timestamp = float(timestamp)
        distance = self._distance(sequence, flow.next_sequence)
        if distance < 0:
            if distance + len(payload) <= 0:
                return TcpReassemblyResult(flow_key, b"", flow.next_sequence, self._buffered_bytes, True)
            raise TcpReassemblyError("segment overlaps already delivered stream bytes")
        if distance == 0:
            output = bytearray(payload)
            flow.next_sequence = (flow.next_sequence + len(payload)) & 0xFFFFFFFF
            while flow.next_sequence in flow.pending:
                chunk = flow.pending.pop(flow.next_sequence)
                flow.pending_bytes -= len(chunk)
                self._buffered_bytes -= len(chunk)
                output.extend(chunk)
                flow.next_sequence = (flow.next_sequence + len(chunk)) & 0xFFFFFFFF
            if len(output) > self.max_flow_bytes:
                raise TcpReassemblyError("contiguous output exceeds per-flow limit")
            return TcpReassemblyResult(flow_key, bytes(output), flow.next_sequence, self._buffered_bytes)
        if distance + len(payload) > self.max_flow_bytes:
            raise TcpReassemblyError("segment exceeds per-flow sequence window")
        for pending_sequence, pending_payload in flow.pending.items():
            existing_distance = self._distance(pending_sequence, flow.next_sequence)
            left = max(distance, existing_distance)
            right = min(distance + len(payload), existing_distance + len(pending_payload))
            if left < right:
                if distance == existing_distance and payload == pending_payload:
                    return TcpReassemblyResult(flow_key, b"", flow.next_sequence, self._buffered_bytes, True)
                raise TcpReassemblyError("overlapping TCP segments are ambiguous")
        if len(flow.pending) >= self.max_pending_segments:
            raise TcpReassemblyError("maximum pending TCP segment count exceeded")
        if flow.pending_bytes + len(payload) > self.max_flow_bytes:
            raise TcpReassemblyError("per-flow buffer limit exceeded")
        if self._buffered_bytes + len(payload) > self.max_buffered_bytes:
            raise TcpReassemblyError("global TCP buffer limit exceeded")
        flow.pending[sequence] = payload
        flow.pending_bytes += len(payload)
        self._buffered_bytes += len(payload)
        return TcpReassemblyResult(flow_key, b"", flow.next_sequence, self._buffered_bytes)
