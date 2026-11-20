import unittest

from defense.network.pcap.tcp_reassembly import TcpReassemblyError, TcpStreamReassembler


FLOW = ("192.0.2.10", 50000, "192.0.2.20", 443)


class TcpStreamReassemblerTests(unittest.TestCase):
    def test_reassembles_in_order_and_out_of_order_segments(self):
        stream = TcpStreamReassembler()
        first = stream.feed(FLOW, 100, b"hello ", initial_sequence=100, timestamp=1)
        self.assertEqual(first.data, b"hello ")
        buffered = stream.feed(FLOW, 112, b"world", timestamp=2)
        self.assertEqual(buffered.data, b"")
        completed = stream.feed(FLOW, 106, b"there ", timestamp=3)
        self.assertEqual(completed.data, b"there world")
        self.assertEqual(completed.next_sequence, 117)
        self.assertEqual(completed.buffered_bytes, 0)

    def test_handles_sequence_wraparound(self):
        stream = TcpStreamReassembler()
        stream.feed(FLOW, 0xFFFFFFFC, b"ab", initial_sequence=0xFFFFFFFC)
        result = stream.feed(FLOW, 0xFFFFFFFE, b"cdef")
        self.assertEqual(result.data, b"cdef")
        self.assertEqual(result.next_sequence, 2)

    def test_ignores_fully_retransmitted_data(self):
        stream = TcpStreamReassembler()
        stream.feed(FLOW, 10, b"hello", initial_sequence=10)
        result = stream.feed(FLOW, 10, b"hello")
        self.assertTrue(result.duplicate)
        self.assertEqual(result.data, b"")

    def test_rejects_partial_and_pending_overlaps(self):
        stream = TcpStreamReassembler()
        stream.feed(FLOW, 100, b"abcd", initial_sequence=100)
        with self.assertRaises(TcpReassemblyError):
            stream.feed(FLOW, 102, b"cdef")
        stream.feed(FLOW, 110, b"abcd")
        with self.assertRaises(TcpReassemblyError):
            stream.feed(FLOW, 112, b"cd")

    def test_enforces_global_and_per_flow_limits(self):
        stream = TcpStreamReassembler(max_buffered_bytes=6, max_flow_bytes=8, max_segment_bytes=8)
        stream.feed(FLOW, 14, b"1234", initial_sequence=10)
        other_flow = ("192.0.2.30", 50001, "192.0.2.40", 443)
        with self.assertRaises(TcpReassemblyError):
            stream.feed(other_flow, 23, b"123", initial_sequence=20, timestamp=1)
        small = TcpStreamReassembler(max_buffered_bytes=4, max_flow_bytes=4, max_segment_bytes=4)
        small.feed(FLOW, 10, b"ab", initial_sequence=10)
        with self.assertRaises(TcpReassemblyError):
            small.feed(FLOW, 13, b"cde")

    def test_expires_idle_flows_and_releases_buffer(self):
        stream = TcpStreamReassembler(max_idle_seconds=5)
        stream.feed(FLOW, 10, b"later", initial_sequence=10, timestamp=1)
        self.assertEqual(stream.expire(7), 1)
        self.assertEqual(stream.buffered_bytes, 0)

    def test_rejects_invalid_inputs_and_time_regression(self):
        stream = TcpStreamReassembler()
        with self.assertRaises(TcpReassemblyError):
            stream.feed(FLOW, -1, b"x")
        with self.assertRaises(TcpReassemblyError):
            stream.feed(FLOW, 1, bytearray(b"x"))
        stream.feed(FLOW, 10, b"x", initial_sequence=10, timestamp=2)
        with self.assertRaises(TcpReassemblyError):
            stream.feed(FLOW, 11, b"x", timestamp=1)

    def test_rejects_flow_and_pending_segment_exhaustion(self):
        stream = TcpStreamReassembler(max_flows=1, max_buffered_bytes=20, max_flow_bytes=20,
                                      max_segment_bytes=20, max_pending_segments=1)
        stream.feed(FLOW, 10, b"a", initial_sequence=10)
        stream.feed(FLOW, 12, b"b")
        with self.assertRaises(TcpReassemblyError):
            stream.feed(FLOW, 14, b"c")
        with self.assertRaises(TcpReassemblyError):
            stream.feed(("192.0.2.1", 1, "192.0.2.2", 2), 1, b"x", initial_sequence=1)


if __name__ == "__main__":
    unittest.main()
