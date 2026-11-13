import unittest
from dataclasses import dataclass
from unittest.mock import Mock

from core.yuki_intelligence import YukiIntelligenceOrchestrator


@dataclass(frozen=True)
class FakeBrief:
    owner: str
    goal: str

    def to_untrusted_context(self):
        return f"UNTRUSTED BRIEF for {self.owner}: {self.goal}"


class YukiIntelligenceOrchestratorTests(unittest.TestCase):
    def setUp(self):
        self.brief = FakeBrief("user-123", "Summarize my notes")
        self.context = Mock()
        self.context.prepare.return_value = self.brief
        self.execution = Mock()
        self.execution.run.return_value = {"status": "completed", "text": "Summary"}
        self.orchestrator = YukiIntelligenceOrchestrator(
            cognitive_context=self.context,
            execution_loop=self.execution,
        )

    def test_composes_owner_scoped_brief_into_guarded_execution(self):
        result = self.orchestrator.run(
            owner_id=" user-123 ",
            user_text=" Summarize my notes ",
            tools=[{"name": "search_notes"}],
            untrusted_context=("Retrieved note text",),
        )
        self.context.prepare.assert_called_once_with(
            owner_id="user-123", goal="Summarize my notes"
        )
        kwargs = self.execution.run.call_args.kwargs
        self.assertEqual(kwargs["user_text"], "Summarize my notes")
        self.assertEqual(
            kwargs["untrusted_context"],
            ("Retrieved note text", "UNTRUSTED BRIEF for user-123: Summarize my notes"),
        )
        self.assertIs(result.cognitive_brief, self.brief)
        self.assertEqual(result.execution["status"], "completed")

    def test_forwards_cancellation_and_streaming_callbacks(self):
        on_delta = Mock()
        cancelled = Mock(return_value=False)
        self.orchestrator.run(
            owner_id="user-123",
            user_text="Continue",
            tools=[],
            on_text_delta=on_delta,
            cancellation_requested=cancelled,
        )
        kwargs = self.execution.run.call_args.kwargs
        self.assertIs(kwargs["on_text_delta"], on_delta)
        self.assertIs(kwargs["cancellation_requested"], cancelled)

    def test_rejects_missing_verified_owner_before_context_access(self):
        with self.assertRaises(ValueError):
            self.orchestrator.run(owner_id="", user_text="Hello", tools=[])
        self.context.prepare.assert_not_called()
        self.execution.run.assert_not_called()

    def test_context_failure_fails_closed_without_model_execution(self):
        self.context.prepare.side_effect = PermissionError("memory consent required")
        with self.assertRaises(PermissionError):
            self.orchestrator.run(owner_id="user-123", user_text="Hello", tools=[])
        self.execution.run.assert_not_called()

    def test_requires_cognitive_brief_serialization(self):
        self.context.prepare.return_value = object()
        with self.assertRaises(TypeError):
            self.orchestrator.run(owner_id="user-123", user_text="Hello", tools=[])
        self.execution.run.assert_not_called()

    def test_constructor_requires_existing_core_contracts(self):
        with self.assertRaises(TypeError):
            YukiIntelligenceOrchestrator(cognitive_context=object(), execution_loop=self.execution)
        with self.assertRaises(TypeError):
            YukiIntelligenceOrchestrator(cognitive_context=self.context, execution_loop=object())


if __name__ == "__main__":
    unittest.main()
