import unittest

from core.observation_event_buffer import ObservationEventBuffer
from core.observation_sources import ObservationEvent


class ObservationEventBufferTests(unittest.TestCase):
    def event(self, app="editor"):
        return ObservationEvent("desktop:metadata", "application", {"app": app})

    def test_nonblocking_callable_handoff_and_fifo_drain(self):
        buffer = ObservationEventBuffer(capacity=3)
        first, second = self.event("one"), self.event("two")
        buffer(first)
        buffer(second)
        self.assertEqual(len(buffer), 2)
        self.assertEqual(buffer.get_nowait(), first)
        self.assertEqual(buffer.drain(limit=2), (second,))
        self.assertIsNone(buffer.get_nowait())

    def test_capacity_is_bounded_and_overflow_is_visible(self):
        buffer = ObservationEventBuffer(capacity=1)
        buffer(self.event())
        with self.assertRaisesRegex(BufferError, "full"):
            buffer(self.event("second"))
        self.assertEqual(len(buffer), 1)

    def test_close_rejects_new_events_and_clears_by_default(self):
        buffer = ObservationEventBuffer(capacity=2)
        buffer(self.event())
        buffer.close()
        self.assertTrue(buffer.closed)
        self.assertEqual(len(buffer), 0)
        with self.assertRaisesRegex(RuntimeError, "closed"):
            buffer(self.event())

    def test_close_can_preserve_buffered_events_for_drain(self):
        buffer = ObservationEventBuffer(capacity=2)
        event = self.event()
        buffer(event)
        buffer.close(clear=False)
        self.assertEqual(buffer.get_nowait(), event)
        self.assertIsNone(buffer.get_nowait())

    def test_rejects_invalid_capacity_and_event(self):
        with self.assertRaises(ValueError):
            ObservationEventBuffer(capacity=0)
        buffer = ObservationEventBuffer()
        with self.assertRaises(TypeError):
            buffer({"not": "an event"})
        with self.assertRaises(ValueError):
            buffer.drain(limit=0)


if __name__ == "__main__":
    unittest.main()
