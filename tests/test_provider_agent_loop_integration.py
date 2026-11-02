import json
import unittest

from core.agent_execution_loop import AgentExecutionLoop
from core.guarded_tool_runtime import GuardedToolRuntime
from core.structured_tool_protocol import StructuredToolProtocol
from providers.agent_adapter import ProviderAgentAdapter


class AllowPolicy:
    def authorize_tool(self, name):
        return name == "echo"


class ScriptedProvider:
    def __init__(self):
        self.calls = 0
        self.transcripts = []

    def generate_chat(self, *, messages, tools, tool_choice):
        self.transcripts.append(messages)
        self.calls += 1
        if self.calls == 1:
            return {"text": "", "tool_calls": {"tool_calls": [
                {"id": "call_1", "name": "echo", "arguments": {"value": "hello"}}
            ]}}
        return {"text": "The tool returned hello.", "tool_calls": {"tool_calls": []}}


class ProviderAgentLoopIntegrationTests(unittest.TestCase):
    def test_provider_transcript_runs_through_guarded_tool_protocol(self):
        invocations = []
        runtime = GuardedToolRuntime(
            {"echo": lambda raw: invocations.append(json.loads(raw)) or raw},
            AllowPolicy(),
        )
        protocol = StructuredToolProtocol(runtime)
        provider = ScriptedProvider()
        adapter = ProviderAgentAdapter(provider)
        tools = [{
            "type": "function",
            "function": {
                "name": "echo", "description": "Echo a value",
                "parameters": {"type": "object", "properties": {
                    "value": {"type": "string"}}, "required": ["value"],
                    "additionalProperties": False},
            },
        }]
        result = AgentExecutionLoop(adapter, protocol).run("Echo hello", tools=tools)
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.text, "The tool returned hello.")
        self.assertEqual(result.tool_calls, 1)
        self.assertEqual(invocations, [{"value": "hello"}])
        self.assertEqual(len(provider.transcripts), 2)
        self.assertIn("untrusted evidence", provider.transcripts[1][-1]["content"])


if __name__ == "__main__":
    unittest.main()
