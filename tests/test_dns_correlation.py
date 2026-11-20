import unittest
from types import SimpleNamespace

from defense.network.pcap.dns import DnsMessage, DnsQuestion
from defense.network.pcap.dns_correlation import correlate_dns_messages


def message(*, response=False, ident=7, name="example.test", rcode=0):
    return DnsMessage(
        transaction_id=ident, is_response=response, opcode=0,
        response_code=rcode, truncated=False, recursion_desired=True,
        recursion_available=response,
        questions=(DnsQuestion(name, 1, 1),), answers=(), authorities=(), additionals=(),
    )


def packet(index, timestamp, *, response=False, ident=7, name="example.test",
           source="192.0.2.10", destination="192.0.2.53",
           source_port=53000, destination_port=53):
    metadata = SimpleNamespace(
        transport_protocol="udp", source=source, destination=destination,
        source_port=source_port, destination_port=destination_port,
    )
    return SimpleNamespace(
        index=index, timestamp=timestamp, metadata=metadata,
        dns=message(response=response, ident=ident, name=name),
    )


class DnsCorrelationTests(unittest.TestCase):
    def test_correlates_request_and_response_with_reversed_endpoints(self):
        result = correlate_dns_messages([
            packet(0, 1.0),
            packet(1, 1.125, response=True, source="192.0.2.53",
                   destination="192.0.2.10", source_port=53, destination_port=53000),
        ])
        self.assertEqual(len(result.transactions), 1)
        transaction = result.transactions[0]
        self.assertEqual((transaction.query_packet_index, transaction.response_packet_index), (0, 1))
        self.assertEqual(transaction.latency_seconds, 0.125)
        self.assertEqual(result.unmatched_query_packet_indices, ())
        self.assertEqual(result.unmatched_response_packet_indices, ())

    def test_does_not_match_different_questions_or_endpoints(self):
        result = correlate_dns_messages([
            packet(0, 1.0),
            packet(1, 1.1, response=True, name="other.test",
                   source="192.0.2.53", destination="192.0.2.10",
                   source_port=53, destination_port=53000),
        ])
        self.assertEqual(result.transactions, ())
        self.assertEqual(result.unmatched_query_packet_indices, (0,))
        self.assertEqual(result.unmatched_response_packet_indices, (1,))

    def test_duplicate_outstanding_ids_are_matched_fifo(self):
        result = correlate_dns_messages([
            packet(0, 1.0), packet(1, 1.01),
            packet(2, 1.1, response=True, source="192.0.2.53",
                   destination="192.0.2.10", source_port=53, destination_port=53000),
            packet(3, 1.2, response=True, source="192.0.2.53",
                   destination="192.0.2.10", source_port=53, destination_port=53000),
        ])
        self.assertEqual(
            [(item.query_packet_index, item.response_packet_index) for item in result.transactions],
            [(0, 2), (1, 3)],
        )

    def test_expires_old_queries_and_bounds_pending_queue(self):
        result = correlate_dns_messages([
            packet(0, 1.0), packet(1, 2.0), packet(2, 2.1, ident=8),
        ], max_pending=1, max_age_seconds=0.5)
        self.assertEqual(result.expired_query_count, 2)
        self.assertEqual(result.unmatched_query_packet_indices, (2,))

    def test_rejects_invalid_limits(self):
        with self.assertRaises(ValueError):
            correlate_dns_messages([], max_pending=0)
        with self.assertRaises(ValueError):
            correlate_dns_messages([], max_age_seconds=0)


if __name__ == "__main__":
    unittest.main()
