import unittest
from core.yuki_ai import YukiAI


class FakeProvider:
    def __init__(self, response="Yuki is ready.", available=True):
        self.response=response; self.available=available; self.prompts=[]
    def generate(self,prompt):
        self.prompts.append(prompt); return self.response
    def is_available(self): return self.available


class YukiAITests(unittest.TestCase):
    def test_answer_uses_provider_and_bounded_memory_context(self):
        provider=FakeProvider()
        ai=YukiAI(provider,memory_search=lambda q:["prior fact","x"*3000])
        self.assertEqual(ai.answer("Hello"),"Yuki is ready.")
        self.assertIn("prior fact",provider.prompts[0])
        self.assertLessEqual(provider.prompts[0].count("x"),500)

    def test_structured_vector_memory_results_are_included_as_context(self):
        provider = FakeProvider()
        ai = YukiAI(provider, memory_search=lambda query: [
            {"id": "m1", "text": "Yuki uses shared desktop services", "score": 0.8}
        ])
        ai.answer("How does Yuki desktop work?")
        self.assertIn("Yuki uses shared desktop services", provider.prompts[0])

    def test_memory_failure_does_not_break_chat(self):
        ai=YukiAI(FakeProvider(),memory_search=lambda q:(_ for _ in ()).throw(RuntimeError()))
        self.assertEqual(ai.answer("Hello"),"Yuki is ready.")

    def test_answer_rejects_empty_and_oversized_input(self):
        ai=YukiAI(FakeProvider())
        for value in ("", "  ", "x"*20001, None):
            with self.subTest(value=str(value)[:10]), self.assertRaises(ValueError):
                ai.answer(value)

    def test_invalid_provider_output_is_rejected(self):
        for response in ("", "x"*40001, None):
            with self.subTest(response=str(response)[:10]), self.assertRaises(RuntimeError):
                YukiAI(FakeProvider(response)).answer("Hello")

    def test_memory_write_is_best_effort(self):
        ai=YukiAI(FakeProvider(),memory_store=lambda q,a:(_ for _ in ()).throw(RuntimeError()))
        self.assertEqual(ai.answer("Hello"),"Yuki is ready.")

    def test_plan_uses_planner_and_memory(self):
        ai=YukiAI(FakeProvider(),memory_search=lambda q:["existing architecture"])
        plan=ai.plan("Research the architecture and report findings")
        self.assertEqual(plan.memory_context,("existing architecture",))

    def test_task_execution_is_not_implicitly_enabled(self):
        ai=YukiAI(FakeProvider())
        with self.assertRaisesRegex(RuntimeError,"not configured"):
            ai.run_task("change files")

    def test_status_is_truthful(self):
        status=YukiAI(FakeProvider()).status()
        self.assertEqual(status["service"],"yuki-ai")
        self.assertFalse(status["task_execution_configured"])
        self.assertTrue(status["tool_execution_requires_existing_approval_gate"])


if __name__=="__main__": unittest.main()
