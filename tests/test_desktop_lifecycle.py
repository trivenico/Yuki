import unittest

from ui.desktop.lifecycle import DesktopServiceLifecycle, DesktopShutdownError


class Resource:
    def __init__(self, name, events, error=None):
        self.name = name
        self.events = events
        self.error = error

    def close(self):
        self.events.append(self.name)
        if self.error:
            raise self.error


class DesktopServiceLifecycleTests(unittest.TestCase):
    def test_closes_in_reverse_order_and_deduplicates_shared_resources(self):
        events = []
        first = Resource("first", events)
        second = Resource("second", events)
        lifecycle = DesktopServiceLifecycle((first, second, first))

        lifecycle.close()
        lifecycle.close()

        self.assertEqual(events, ["first", "second"])
        self.assertTrue(lifecycle.closed)

    def test_cleanup_continues_after_a_resource_fails(self):
        events = []
        first = Resource("first", events)
        broken = Resource("broken", events, RuntimeError("close failed"))
        last = Resource("last", events)
        lifecycle = DesktopServiceLifecycle((first, broken, last))

        with self.assertRaises(DesktopShutdownError):
            lifecycle.close()

        self.assertEqual(events, ["last", "broken", "first"])
        self.assertTrue(lifecycle.closed)
        lifecycle.close()
        self.assertEqual(events, ["last", "broken", "first"])

    def test_non_closable_resources_are_ignored(self):
        events = []
        lifecycle = DesktopServiceLifecycle((object(), Resource("closable", events)))
        lifecycle.close()
        self.assertEqual(events, ["closable"])


if __name__ == "__main__":
    unittest.main()
