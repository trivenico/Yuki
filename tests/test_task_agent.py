import json
import unittest

from core.task_agent import ApprovedTaskAgent
from core.agent import AgentResponse


class FakeProvider:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = 0

    def generate(self, prompt):
        self.calls += 1
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

    def test_duplicate_json_keys_are_rejected(self):
        skills = FakeSkillAgent()
        raw = '{"type":"final","type":"tool_call","skill":"time","argument":""}'
        result = ApprovedTaskAgent(FakeProvider([raw]), skills).run("Do a task")
        self.assertEqual(result.status, "invalid_model_output")
        self.assertEqual(skills.calls, [])

    def test_unknown_fields_are_rejected(self):
        skills = FakeSkillAgent()
        raw = json.dumps({"type": "tool_call", "skill": "time", "argument": "", "shell": "ignored"})
        result = ApprovedTaskAgent(FakeProvider([raw]), skills).run("Do a task")
        self.assertEqual(result.status, "invalid_model_output")
        self.assertEqual(skills.calls, [])

    def test_final_schema_requires_exact_fields(self):
        skills = FakeSkillAgent()
        raw = json.dumps({"type": "final", "message": "ok", "skill": "time"})
        result = ApprovedTaskAgent(FakeProvider([raw]), skills).run("Say hello")
        self.assertEqual(result.status, "invalid_model_output")
        self.assertEqual(skills.calls, [])

    def test_excessively_nested_json_is_rejected_without_tool_execution(self):
        skills = FakeSkillAgent()
        raw = '{"type":"final","message":' + ('[' * 5000) + '0' + (']' * 5000) + '}'
        result = ApprovedTaskAgent(FakeProvider([raw]), skills).run("Do a task")
        self.assertEqual(result.status, "invalid_model_output")
        self.assertEqual(skills.calls, [])

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

    def test_bounds_step_count(self):
        with self.assertRaises(ValueError):
            ApprovedTaskAgent(FakeProvider([]), FakeSkillAgent(), max_steps=6)


if __name__ == "__main__":
    unittest.main()
