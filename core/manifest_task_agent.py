"""Manifest task agent that plans and proposes tool calls for approval."""
import json


class PlanResult:
    def __init__(self, action_type, skill_id, arguments):
        self.action_type = action_type
        self.skill_id = skill_id
        self.arguments = arguments
        self.action = {
            "skill_id": skill_id,
            "arguments": arguments,
        }


class ManifestTaskAgent:
    def __init__(self, provider, agent, dispatcher):
        self.provider = provider
        self.agent = agent
        self.dispatcher = dispatcher

    def run(self, goal: str):
        response_str = self.provider.generate(goal)
        data = json.loads(response_str)
        return PlanResult(
            action_type=data.get("type", "tool_call"),
            skill_id=data.get("skill_id"),
            arguments=data.get("arguments", {}),
        )

    def execute_proposed(self, action: dict):
        skill_id = action.get("skill_id")
        arguments = action.get("arguments", {})
        return self.dispatcher.dispatch(skill_id, arguments)
