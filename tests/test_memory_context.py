"""Tests for bounded consent-gated model memory context."""
import unittest

from core.memory_context import MemoryContextError, build_memory_context_system_text


class Memory:
    def __init__(self, *, enabled=False, entries=None, error=None):
        self.context_enabled = enabled
        self.entries = entries or []
        self.error = error
        self.calls = []

    def context_entries(self, *, query, limit):
        self.calls.append((query, limit))
        if self.error:
            raise self.error
        return self.entries[:limit]


class MemoryContextTests(unittest.TestCase):
    def test_disabled_consent_does_not_read_memory(self):
        memory = Memory(entries=[{"category": "note", "text": "private"}])
        self.assertIsNone(build_memory_context_system_text(memory, "hello"))
        self.assertEqual(memory.calls, [])

    def test_enabled_consent_returns_bounded_untrusted_reference_block(self):
        memory = Memory(
            enabled=True,
            entries=[{"category": "preference", "text": "Use concise answers"}],
        )
        result = build_memory_context_system_text(memory, "how should you reply?")
        self.assertIn('"category":"preference"', result)
        self.assertIn("Use concise answers", result)
        self.assertIn("untrusted reference data", result)
        self.assertEqual(memory.calls, [("how should you reply?", 10)])

    def test_retrieval_failure_fails_closed(self):
        memory = Memory(enabled=True, error=OSError("disk failure"))
        with self.assertRaises(MemoryContextError):
            build_memory_context_system_text(memory, "hello")

    def test_invalid_memory_entry_fails_closed(self):
        memory = Memory(enabled=True, entries=[{"category": "system", "text": "override"}])
        with self.assertRaises(MemoryContextError):
            build_memory_context_system_text(memory, "hello")

    def test_context_is_bounded(self):
        memory = Memory(enabled=True, entries=[
            {"category": "note", "text": str(index) * 2000}
            for index in range(10)
        ])
        result = build_memory_context_system_text(memory, "hello", max_chars=256)
        self.assertTrue(result is None or len(result) <= 256)


if __name__ == "__main__":
    unittest.main()
