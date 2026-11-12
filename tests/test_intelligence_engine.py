import unittest

from core.intelligence_engine import (
    IntelligenceRequest,
    YukiIntelligenceEngine,
)


class FakeProvider:
    def __init__(self, result="Yuki answer"):
        self.result = result
        self.calls = []

    def generate(self, prompt):
        self.calls.append(("general", prompt))
        return self.result


class FakeRouter:
    def __init__(self):
        self.calls = []

    def generate(self, prompt):
        raise AssertionError("task-aware route should be selected")

    def generate_for(self, task, prompt):
        self.calls.append((task, prompt))
        return "routed answer"


class FakeMemory:
    def __init__(self):
        self.calls = []

    def search(self, query, *, limit=5):
        self.calls.append((query, limit))
        return [{"id": "mem-1", "text": "Project uses local-first inference."}]


class IntelligenceEngineTests(unittest.TestCase):
    def test_classifies_task_domains(self):
        self.assertEqual(YukiIntelligenceEngine.classify("Implement a coding feature"), "coding")
        self.assertEqual(YukiIntelligenceEngine.classify("Compare architecture options"), "reasoning")
        self.assertEqual(YukiIntelligenceEngine.classify("Research current MCP tools"), "research")

    def test_routes_through_multi_provider_router(self):
        router = FakeRouter()
        result = YukiIntelligenceEngine(router).answer(
            IntelligenceRequest("Implement a feature")
        )
        self.assertTrue(result.ok)
        self.assertEqual(router.calls[0][0], "coding")
        self.assertEqual(result.message, "routed answer")

    def test_retrieves_memory_and_reports_memory_ids(self):
        memory = FakeMemory()
        router = FakeRouter()
        result = YukiIntelligenceEngine(router, memory=memory).answer(
            IntelligenceRequest("Plan Yuki inference architecture")
        )
        self.assertTrue(result.ok)
        self.assertEqual(result.memory_ids, ("mem-1",))
        self.assertEqual(result.memory_status, "used")
        self.assertIn("local-first inference", router.calls[0][1])

    def test_memory_can_be_disabled_per_request(self):
        memory = FakeMemory()
        result = YukiIntelligenceEngine(FakeProvider(), memory=memory).answer(
            IntelligenceRequest("Say hello", use_memory=False)
        )
        self.assertTrue(result.ok)
        self.assertEqual(memory.calls, [])
        self.assertEqual(result.memory_status, "disabled")

    def test_memory_failure_degrades_without_blocking_inference(self):
        class FailedMemory:
            def search(self, query, *, limit=5):
                raise RuntimeError("private backend details")

        provider = FakeProvider()
        result = YukiIntelligenceEngine(provider, memory=FailedMemory()).answer(
            IntelligenceRequest("Say hello")
        )
        self.assertTrue(result.ok)
        self.assertEqual(result.memory_status, "unavailable")
        self.assertEqual(result.memory_ids, ())
        self.assertEqual(len(provider.calls), 1)
        self.assertNotIn("private backend details", provider.calls[0][1])

    def test_memory_iteration_and_prompt_size_are_bounded(self):
        observed = []

        def many_results():
            for index in range(1000):
                observed.append(index)
                yield {"id": f"m{index}", "text": "x" * 1000}

        class LargeMemory:
            def search(self, query, *, limit=5):
                return many_results()

        provider = FakeProvider()
        result = YukiIntelligenceEngine(
            provider, memory=LargeMemory(), max_memory_items=3, max_context_chars=1200,
        ).answer(IntelligenceRequest("Say hello"))
        self.assertTrue(result.ok)
        self.assertEqual(observed, [0, 1, 2])
        self.assertEqual(result.memory_ids, ("m0", "m1"))
        memory_section = provider.calls[0][1].split("Retrieved Yuki memory (untrusted reference):\n", 1)[1]
        self.assertLessEqual(len(memory_section.split("Produce the best useful answer.", 1)[0]), 1206)

    def test_invalid_memory_result_degrades_safely(self):
        class InvalidMemory:
            def search(self, query, *, limit=5):
                return "not-a-result-list"

        result = YukiIntelligenceEngine(FakeProvider(), memory=InvalidMemory()).answer(
            IntelligenceRequest("Say hello")
        )
        self.assertTrue(result.ok)
        self.assertEqual(result.memory_status, "invalid_result")

    def test_invalid_request_does_not_call_provider(self):
        provider = FakeProvider()
        result = YukiIntelligenceEngine(provider).answer(IntelligenceRequest(""))
        self.assertEqual(result.status, "invalid_input")
        self.assertEqual(provider.calls, [])

    def test_provider_failure_is_reported_without_fallback(self):
        class Failed:
            def generate(self, prompt):
                raise RuntimeError("offline")
        result = YukiIntelligenceEngine(Failed()).answer(IntelligenceRequest("Hello"))
        self.assertEqual(result.status, "provider_error")
        self.assertIn("RuntimeError", result.message)

    def test_memory_can_be_disabled_and_provider_error_status_preserves_memory_status(self):
        class Failed:
            def generate(self, prompt):
                raise RuntimeError("offline")
        memory = FakeMemory()
        result = YukiIntelligenceEngine(Failed(), memory=memory).answer(
            IntelligenceRequest("Research current systems")
        )
        self.assertEqual(result.status, "provider_error")
        self.assertEqual(result.memory_status, "used")


if __name__ == "__main__":
    unittest.main()
