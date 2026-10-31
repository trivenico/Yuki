import json
import tempfile
import unittest
from pathlib import Path

from core.task_ledger import TaskLedger

from core.task_agent import ApprovedTaskAgent
from core.agent import AgentResponse


class FakeProvider:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = 0
        self.prompts = []

    def generate(self, prompt):
        self.calls += 1
        self.prompts.append(prompt)
        return self.outputs.pop(0)


class FakeSkillAgent:
    def __init__(self, response=None):
        self.calls = []
        self.response = response or AgentResponse(True, "read complete", "time", {"now": "test"})

    def available_skills(self):
        return [{"name": "time", "description": "Read current time", "risk": "low"}]

    def run_skill(self, name, argument=""):
        self.calls.append((name, argument))
        return self.response


class ApprovedTaskAgentTests(unittest.TestCase):
    def test_final_response_executes_no_tools(self):
        skills = FakeSkillAgent()
        runner = ApprovedTaskAgent(FakeProvider(['{"type":"final","message":"hello"}']), skills)
        result = runner.run("Say hello")
        self.assertTrue(result.ok)
        self.assertEqual(skills.calls, [])
        self.assertEqual(result.status, "completed")

    def test_only_registered_skill_can_be_requested(self):
        skills = FakeSkillAgent()
        provider = FakeProvider([json.dumps({"type": "tool_call", "skill": "shell", "argument": "id"})])
        result = ApprovedTaskAgent(provider, skills).run("Do a task")
        self.assertEqual(result.status, "blocked")
        self.assertEqual(skills.calls, [])

    def test_tool_calls_pass_through_guarded_skill_agent(self):
        skills = FakeSkillAgent()
        provider = FakeProvider([
            json.dumps({"type": "tool_call", "skill": "time", "argument": ""}),
            json.dumps({"type": "final", "message": "The time skill returned a result."}),
        ])
        result = ApprovedTaskAgent(provider, skills, max_steps=2).run("What time is it?")
        self.assertTrue(result.ok)
        self.assertEqual(skills.calls, [("time", "")])
        self.assertEqual(provider.calls, 2)

    def test_council_context_is_advisory_input_to_task_planning(self):
        skills = FakeSkillAgent()
        provider = FakeProvider(['{"type":"final","message":"Use the reviewed plan."}'])
        result = ApprovedTaskAgent(provider, skills).run(
            "Improve reliability",
            council_context="Consensus: add regression tests first.",
        )
        self.assertTrue(result.ok)
        self.assertIn("Consensus: add regression tests first.", provider.prompts[0])
        self.assertIn("never authorization", provider.prompts[0])
        self.assertEqual(skills.calls, [])

    def test_duplicate_json_keys_never_execute_a_tool(self):
        skills = FakeSkillAgent()
        raw = '{"type":"final","type":"tool_call","skill":"time","argument":""}'
        result = ApprovedTaskAgent(FakeProvider([raw]), skills).run("Do a task")
        self.assertEqual(result.status, "invalid_model_output")
        self.assertEqual(skills.calls, [])

    def test_unexpected_tool_fields_never_execute_a_tool(self):
        skills = FakeSkillAgent()
        raw = json.dumps({"type": "tool_call", "skill": "time", "argument": "", "shell": "id"})
        result = ApprovedTaskAgent(FakeProvider([raw]), skills).run("Do a task")
        self.assertEqual(result.status, "invalid_model_output")
        self.assertEqual(skills.calls, [])

    def test_ledger_audits_goal_and_arguments_without_persisting_plaintext(self):
        with tempfile.TemporaryDirectory() as temp:
            ledger = TaskLedger(Path(temp) / "tasks.sqlite3")
            provider = FakeProvider([
                json.dumps({"type": "tool_call", "skill": "time", "argument": "private-argument"}),
                json.dumps({"type": "final", "message": "Done."}),
            ])
            result = ApprovedTaskAgent(provider, FakeSkillAgent(), max_steps=2, ledger=ledger).run(
                "private user objective"
            )
            self.assertEqual(result.status, "awaiting_verification")
            task = ledger.list(limit=1)[0]
            events = ledger.list_events(task["id"])
            serialized = repr(events)
            self.assertEqual(task["title"], "AI-assisted task")
            self.assertEqual(task["detail"], "")
            self.assertNotIn("private user objective", repr(task))
            self.assertNotIn("private user objective", serialized)
            self.assertNotIn("private-argument", serialized)
            started = events[0]["payload"]
            proposed = next(event["payload"] for event in events if event["event_type"] == "tool.proposed")
            observed = next(event["payload"] for event in events if event["event_type"] == "tool.observed")
            self.assertEqual(len(started["goal_sha256"]), 64)
            self.assertEqual(len(proposed["argument_sha256"]), 64)
            self.assertEqual(proposed["argument_chars"], len("private-argument"))
            self.assertNotIn("message", observed)
            self.assertEqual(len(observed["message_sha256"]), 64)

    def test_invalid_json_never_executes_a_tool(self):
        skills = FakeSkillAgent()
        result = ApprovedTaskAgent(FakeProvider(["not JSON"]), skills).run("Do a task")
        self.assertEqual(result.status, "invalid_model_output")
        self.assertEqual(skills.calls, [])

    def test_denied_tool_stops_loop(self):
        from core.agent import AgentResponse
        skills = FakeSkillAgent(AgentResponse(False, "approval denied", "time", stage="permission_denied"))
        provider = FakeProvider([json.dumps({"type": "tool_call", "skill": "time", "argument": ""})])
        result = ApprovedTaskAgent(provider, skills).run("Do a task")
        self.assertEqual(result.status, "approval_denied")
        self.assertEqual(provider.calls, 1)

    def test_ledger_backed_final_response_awaits_independent_verification(self):
        with tempfile.TemporaryDirectory() as temp:
            ledger = TaskLedger(Path(temp) / "tasks.sqlite3")
            provider = FakeProvider(['{"type":"final","message":"The report is ready."}'])
            result = ApprovedTaskAgent(provider, FakeSkillAgent(), ledger=ledger).run("Prepare report")
            self.assertFalse(result.ok)
            self.assertEqual(result.status, "awaiting_verification")
            task = ledger.list(limit=1)[0]
            self.assertEqual(task["status"], "awaiting_verification")
            self.assertEqual(task["result"], "The report is ready.")
            events = ledger.list_events(task["id"])
            self.assertEqual(
                [event["event_type"] for event in events],
                ["task.started", "task.awaiting_verification"],
            )

    def test_ledger_backed_tool_call_records_proposal_and_observation(self):
        with tempfile.TemporaryDirectory() as temp:
            ledger = TaskLedger(Path(temp) / "tasks.sqlite3")
            provider = FakeProvider([
                json.dumps({"type": "tool_call", "skill": "time", "argument": ""}),
                json.dumps({"type": "final", "message": "Time was retrieved."}),
            ])
            result = ApprovedTaskAgent(
                provider, FakeSkillAgent(), max_steps=2, ledger=ledger
            ).run("Retrieve time")
            self.assertEqual(result.status, "awaiting_verification")
            task = ledger.list(limit=1)[0]
            self.assertEqual(task["status"], "awaiting_verification")
            self.assertEqual(
                [event["event_type"] for event in ledger.list_events(task["id"])],
                ["task.started", "tool.proposed", "tool.observed", "task.awaiting_verification"],
            )

    def test_ledger_backed_provider_failure_is_persisted(self):
        class BrokenProvider:
            def generate(self, prompt):
                raise RuntimeError("private provider detail")

        with tempfile.TemporaryDirectory() as temp:
            ledger = TaskLedger(Path(temp) / "tasks.sqlite3")
            result = ApprovedTaskAgent(BrokenProvider(), FakeSkillAgent(), ledger=ledger).run("Do work")
            self.assertEqual(result.status, "provider_error")
            task = ledger.list(limit=1)[0]
            self.assertEqual(task["status"], "failed")
            self.assertEqual(task["error_code"], "provider_error")
            self.assertEqual(ledger.list_events(task["id"])[-1]["event_type"], "task.failed")

    def test_bounds_step_count(self):
        with self.assertRaises(ValueError):
            ApprovedTaskAgent(FakeProvider([]), FakeSkillAgent(), max_steps=6)


if __name__ == "__main__":
    unittest.main()
