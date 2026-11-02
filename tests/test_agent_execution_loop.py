import json
import unittest

from core.agent_execution_loop import AgentExecutionError, AgentExecutionLoop
from core.guarded_tool_runtime import GuardedToolRuntime
from core.structured_tool_protocol import StructuredToolProtocol


class AllowPolicy:
    def authorize_tool(self, name):
        return name == "echo"


class AgentExecutionLoopTests(unittest.TestCase):
    def setUp(self):
        self.invocations = []
        runtime = GuardedToolRuntime(
            {"echo": lambda raw: self.invocations.append(json.loads(raw)) or raw},
            AllowPolicy(),
        )
        self.protocol = StructuredToolProtocol(runtime)
        self.tools = [{
            "type": "function",
            "function": {"name": "echo", "description": "Echo input",
                         "parameters": {"type": "object", "properties": {
                             "value": {"type": "string"}}, "required": ["value"]}},
        }]

    def test_tool_result_is_returned_to_model_before_final_answer(self):
        observed = []
        def model(*, messages, tools, tool_choice):
            observed.append(list(messages))
            if len(observed) == 1:
                return {"text": "", "tool_calls": {"tool_calls": [
                    {"id": "call_1", "name": "echo", "arguments": {"value": "hello"}}
                ]}}
            self.assertIn("untrusted evidence", messages[-1]["content"])
            return {"text": "Finished", "tool_calls": {"tool_calls": []}}

        result = AgentExecutionLoop(model, self.protocol).run("Do it", tools=self.tools)
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.text, "Finished")
        self.assertEqual((result.model_turns, result.tool_calls), (2, 1))
        self.assertEqual(self.invocations, [{"value": "hello"}])
        self.assertEqual(len(observed), 2)

    def test_text_only_model_response_completes_without_tool_execution(self):
        result = AgentExecutionLoop(
            lambda **kwargs: {"text": "Hello", "tool_calls": {"tool_calls": []}},
            self.protocol,
        ).run("Hi", tools=[])
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.text, "Hello")
        self.assertEqual(self.invocations, [])

    def test_model_turn_budget_stops_unfinished_agent(self):
        def model(**kwargs):
            return {"text": "", "tool_calls": {"tool_calls": [
                {"id": "call_1", "name": "echo", "arguments": {"value": "again"}}
            ]}}
        result = AgentExecutionLoop(model, self.protocol, max_model_turns=1).run(
            "Continue", tools=self.tools)
        self.assertEqual(result.status, "limit_reached")
        self.assertEqual(result.model_turns, 1)
        self.assertEqual(result.tool_calls, 1)

    def test_tool_call_budget_prevents_dispatch(self):
        def model(**kwargs):
            return {"text": "", "tool_calls": {"tool_calls": [
                {"id": "call_1", "name": "echo", "arguments": {"value": "x"}},
                {"id": "call_2", "name": "echo", "arguments": {"value": "y"}},
            ]}}
        result = AgentExecutionLoop(model, self.protocol, max_tool_calls=1).run(
            "Do two", tools=self.tools)
        self.assertEqual(result.status, "limit_reached")
        self.assertEqual(self.invocations, [])

    def test_cancellation_before_first_model_call(self):
        model_calls = []
        result = AgentExecutionLoop(
            lambda **kwargs: model_calls.append(True),
            self.protocol,
            cancellation_requested=lambda: True,
        ).run("Stop", tools=[])
        self.assertEqual(result.status, "cancelled")
        self.assertEqual(model_calls, [])

    def test_cancellation_after_model_before_tool_dispatch(self):
        def model(**kwargs):
            return {"text": "", "tool_calls": {"tool_calls": [
                {"id": "call_1", "name": "echo", "arguments": {"value": "x"}}
            ]}}
        checks = iter([False, True])
        result = AgentExecutionLoop(
            model, self.protocol, cancellation_requested=lambda: next(checks)
        ).run("Stop before dispatch", tools=self.tools)
        self.assertEqual(result.status, "cancelled")
        self.assertEqual(self.invocations, [])

    def test_transcript_limit_stops_before_next_model_call(self):
        calls = []
        def model(**kwargs):
            calls.append(True)
            return {"text": "", "tool_calls": {"tool_calls": [
                {"id": "call_1", "name": "echo", "arguments": {"value": "x"}}
            ]}}
        result = AgentExecutionLoop(
            model, self.protocol, max_model_turns=3, max_transcript_chars=150
        ).run("Initial", tools=self.tools)
        self.assertEqual(result.status, "limit_reached")
        self.assertLessEqual(len(calls), 2)

    def test_invalid_model_envelope_is_rejected_without_tool_execution(self):
        for response in (
            {"text": "", "tool_calls": {"tool_calls": [{"id": "bad", "name": "missing", "arguments": []}]}},
            {"text": "", "tool_calls": {"tool_calls": []}, "unexpected": True},
            {"text": 12, "tool_calls": {"tool_calls": []}},
        ):
            with self.subTest(response=response):
                with self.assertRaises(AgentExecutionError):
                    AgentExecutionLoop(lambda **kwargs: response, self.protocol).run(
                        "Test", tools=self.tools)
        self.assertEqual(self.invocations, [])

    def test_model_failure_is_wrapped_and_reported(self):
        events = []
        def fail(**kwargs):
            raise TimeoutError("private provider details")
        with self.assertRaises(AgentExecutionError):
            AgentExecutionLoop(
                fail, self.protocol, on_event=lambda kind, data: events.append((kind, data))
            ).run("Test", tools=[])
        self.assertEqual(events[-1][0], "agent.model_failed")
        self.assertNotIn("private provider details", str(events[-1]))

    def test_event_callback_failure_fails_closed(self):
        def broken_event(*args):
            raise OSError("journal unavailable")
        with self.assertRaises(OSError):
            AgentExecutionLoop(
                lambda **kwargs: {"text": "never called", "tool_calls": {"tool_calls": []}},
                self.protocol, on_event=broken_event,
            ).run("Test", tools=[])

    def test_invalid_limits_are_rejected(self):
        for kwargs in ({"max_model_turns": 0}, {"max_tool_calls": True},
                       {"max_duration_seconds": 0}, {"max_transcript_chars": 0}):
            with self.subTest(kwargs=kwargs), self.assertRaises((TypeError, ValueError)):
                AgentExecutionLoop(lambda **kw: {}, self.protocol, **kwargs)


if __name__ == "__main__":
    unittest.main()
