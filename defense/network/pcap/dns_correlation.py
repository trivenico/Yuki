"""Bounded correlation of DNS request/response metadata in offline captures."""
from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
import math
from typing import Iterable


@dataclass(frozen=True)
class DnsTransaction:
    query_packet_index: int
    response_packet_index: int
    transaction_id: int
    questions: tuple[tuple[str, int, int], ...]
    latency_seconds: float
    response_code: int
    truncated: bool


@dataclass(frozen=True)
class DnsCorrelation:
    transactions: tuple[DnsTransaction, ...]
    unmatched_query_packet_indices: tuple[int, ...]
    unmatched_response_packet_indices: tuple[int, ...]
    expired_query_count: int


def _questions(message: object) -> tuple[tuple[str, int, int], ...]:
    return tuple((q.name, q.record_type, q.record_class) for q in message.questions)


def correlate_dns_messages(
    packets: Iterable[object],
    *,
    max_pending: int = 4096,
    max_age_seconds: float = 30.0,
) -> DnsCorrelation:
    """Correlate parsed UDP DNS messages without retaining packet payloads.

    Correlation is directional and includes the reversed endpoint tuple,
    transaction ID, opcode, and question tuple. Duplicate outstanding requests
    are queued FIFO. Capture order is authoritative; negative timestamp deltas
    are not emitted as valid latency measurements.
    """
    if isinstance(max_pending, bool) or not isinstance(max_pending, int) or not 1 <= max_pending <= 100_000:
        raise ValueError("max_pending is outside supported bounds")
    if isinstance(max_age_seconds, bool) or not isinstance(max_age_seconds, (int, float)) or not 0 < max_age_seconds <= 3600:
        raise ValueError("max_age_seconds is outside supported bounds")

    pending: dict[tuple[object, ...], deque[tuple[int, float]]] = defaultdict(deque)
    pending_count = 0
    expired = 0
    transactions: list[DnsTransaction] = []
    unmatched_responses: list[int] = []
    seen_queries: list[int] = []

    for packet in packets:
        message = getattr(packet, "dns", None)
        meta = getattr(packet, "metadata", None)
        if message is None or meta is None or getattr(meta, "transport_protocol", None) != "udp":
            continue
        source, destination = getattr(meta, "source", None), getattr(meta, "destination", None)
        source_port, destination_port = getattr(meta, "source_port", None), getattr(meta, "destination_port", None)
        if None in (source, destination, source_port, destination_port):
            continue
        timestamp = float(packet.timestamp)
        if not math.isfinite(timestamp):
            raise ValueError("packet timestamps must be finite")
        # Expire stale requests before processing this packet.
        for stale_key in list(pending):
            queue = pending[stale_key]
            while queue and timestamp - queue[0][1] > max_age_seconds:
                queue.popleft()
                pending_count -= 1
                expired += 1
            if not queue:
                del pending[stale_key]
        questions = _questions(message)
        common = (message.transaction_id, message.opcode, questions)
        if not message.is_response:
            key = (source, source_port, destination, destination_port, *common)
            seen_queries.append(packet.index)
            if pending_count >= max_pending:
                # Evict the globally oldest request; memory remains strictly bounded.
                oldest_key = None
                oldest_time = None
                for candidate, queue in pending.items():
                    if queue and (oldest_time is None or queue[0][1] < oldest_time):
                        oldest_key, oldest_time = candidate, queue[0][1]
                if oldest_key is not None:
                    pending[oldest_key].popleft()
                    pending_count -= 1
                    expired += 1
                    if not pending[oldest_key]:
                        del pending[oldest_key]
            pending[key].append((packet.index, timestamp))
            pending_count += 1
            continue

        key = (destination, destination_port, source, source_port, *common)
        queue = pending.get(key)
        if not queue:
            unmatched_responses.append(packet.index)
            continue
        query_index, query_time = queue.popleft()
        pending_count -= 1
        if not queue:
            del pending[key]
        latency = timestamp - query_time
        if latency < 0 or latency > max_age_seconds:
            expired += 1
            unmatched_responses.append(packet.index)
            continue
        transactions.append(DnsTransaction(
            query_index, packet.index, message.transaction_id, questions,
            latency, message.response_code, message.truncated,
        ))

    unmatched_queries = [index for queue in pending.values() for index, _ in queue]
    unmatched_queries.sort()
    return DnsCorrelation(tuple(transactions), tuple(unmatched_queries),
                          tuple(unmatched_responses), expired)
