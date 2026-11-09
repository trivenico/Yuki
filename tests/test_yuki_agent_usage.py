import unittest

from yuki_agent import YukiAgent


class FakeUsageTracker:
    def __init__(self, fail=False):
        self.events = []
        self.fail = fail

    def record(self, **event):
        if self.fail:
            raise OSError("usage database unavailable")
        self.events.append(event)


class FakeProvider:
    model = "fake-model"

    def __init__(self, usage=None):
        self.usage = usage
        self.last_usage = None
        self.calls = 0

    def is_available(self):
        return True

    def generate(self, prompt):
        self.calls += 1
        self.last_usage = self.usage
        return "generated response"


class YukiAgentUsageTests(unittest.TestCase):
    def test_records_exact_provider_usage(self):
        tracker = FakeUsageTracker()
        provider = FakeProvider({"input_tokens": 12, "output_tokens": 6, "estimated": False})
        answer = YukiAgent(provider, usage_tracker=tracker).ask("hello")
        self.assertEqual(answer, "generated response")
        self.assertEqual(len(tracker.events), 1)
        self.assertEqual(tracker.events[0]["input_tokens"], 12)
        self.assertFalse(tracker.events[0]["estimated"])

    def test_records_estimated_usage_when_provider_has_no_metadata(self):
        tracker = FakeUsageTracker()
        answer = YukiAgent(FakeProvider(), usage_tracker=tracker).ask("12345678")
        self.assertEqual(answer, "generated response")
        self.assertTrue(tracker.events[0]["estimated"])
        self.assertGreaterEqual(tracker.events[0]["input_tokens"], 2)

    def test_clears_stale_metadata_before_each_generation(self):
        tracker = FakeUsageTracker()
        provider = FakeProvider(usage=None)
        provider.last_usage = {"input_tokens": 99, "output_tokens": 99, "estimated": False}
        agent = YukiAgent(provider, usage_tracker=tracker)
        agent.ask("first")
        self.assertTrue(tracker.events[0]["estimated"])
        self.assertNotEqual(tracker.events[0]["input_tokens"], 99)

    def test_usage_storage_failure_does_not_block_answer(self):
        tracker = FakeUsageTracker(fail=True)
        agent = YukiAgent(FakeProvider(), usage_tracker=tracker)
        self.assertEqual(agent.ask("hello"), "generated response")
        self.assertEqual(agent.last_usage_error, "OSError")

    def test_invalid_prompt_does_not_record_usage(self):
        tracker = FakeUsageTracker()
        self.assertIn("enter a message", YukiAgent(FakeProvider(), usage_tracker=tracker).ask(" "))
        self.assertEqual(tracker.events, [])


if __name__ == "__main__":
    unittest.main()
