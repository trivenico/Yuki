"""Bounded model planner: produces proposals only and never executes during planning."""
import json


MAX_PROMPT_CHARS = 20_000
MAX_RESPONSE_BYTES = 2 * 1024 * 1024


class PlanResult:
    def __init__(self, action_type, skill_id, arguments):
        self.action_type = action_type
        self.skill_id = skill_id
        self.arguments = arguments
        self.action = {"skill_id": skill_id, "arguments": arguments}


class ManifestTaskAgent:
    def __init__(self, provider, agent, dispatcher):
        self.provider = provider
        self.agent = agent
        self.dispatcher = dispatcher

    def run(self, goal: str):
        if not isinstance(goal, str) or not goal.strip():
            raise ValueError("Task goal must be non-empty text.")
        if len(goal) > MAX_PROMPT_CHARS:
            raise ValueError("Task goal exceeds the size limit.")
        response_str = self.provider.generate(goal)
        if not isinstance(response_str, str):
            raise ValueError("Provider returned an invalid response.")
        if len(response_str.encode("utf-8")) > MAX_RESPONSE_BYTES:
            raise ValueError("Provider response exceeds the size limit.")
        try:
            data = json.loads(response_str)
        except (json.JSONDecodeError, TypeError) as exc:
            raise ValueError("Provider returned invalid task JSON.") from exc
        if not isinstance(data, dict):
            raise ValueError("Task proposal must be a JSON object.")
        if data.get("type", "tool_call") != "tool_call":
            raise ValueError("Unsupported task proposal type.")
        skill_id = data.get("skill_id")
        arguments = data.get("arguments", {})
        if not isinstance(skill_id, str) or not skill_id.strip():
            raise ValueError("Task proposal must name a skill.")
        if not isinstance(arguments, dict):
            raise ValueError("Task proposal arguments must be an object.")
        if set(data) - {"type", "skill_id", "arguments"}:
            raise ValueError("Task proposal contains unsupported fields.")
        return PlanResult("tool_call", skill_id, arguments)

    def execute_proposed(self, action: dict, approval_receipt=None):
        if not isinstance(action, dict) or set(action) != {"skill_id", "arguments"}:
            raise ValueError("Invalid approved action snapshot.")
        skill_id = action["skill_id"]
        arguments = action["arguments"]
        if not isinstance(skill_id, str) or not skill_id or not isinstance(arguments, dict):
            raise ValueError("Invalid approved action fields.")
        return self.dispatcher.dispatch(skill_id, arguments, approval_receipt=approval_receipt)
