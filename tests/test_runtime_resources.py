import unittest

from core.runtime_resources import RuntimeResourceGroup


class _Resource:
    def __init__(self, name, events, error=False):
        self.name = name
        self.events = events
        self.error = error

    def close(self):
        self.events.append(self.name)
        if self.error:
            raise OSError("close failed")


class RuntimeResourceGroupTests(unittest.TestCase):
    def test_closes_once_in_reverse_order_and_deduplicates_identity(self):
        events = []
        first = _Resource("first", events)
        second = _Resource("second", events)
        group = RuntimeResourceGroup([first, second, first, object()])
        group.close()
        group.close()
        self.assertEqual(events, ["second", "first"])
        self.assertTrue(group.closed)

    def test_close_attempts_remaining_resources_after_failure(self):
        events = []
        group = RuntimeResourceGroup([
            _Resource("first", events),
            _Resource("broken", events, error=True),
            _Resource("last", events),
        ])
        with self.assertRaisesRegex(RuntimeError, "failed to close"):
            group.close()
        self.assertEqual(events, ["last", "broken", "first"])
        group.close()
        self.assertEqual(len(events), 3)

    def test_rejects_add_after_close_and_none_resources(self):
        group = RuntimeResourceGroup()
        with self.assertRaises(TypeError):
            group.add(None)
        group.close()
        with self.assertRaises(RuntimeError):
            group.add(object())


if __name__ == "__main__":
    unittest.main()